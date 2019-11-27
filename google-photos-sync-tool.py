#!/usr/bin/env python
"""

This tools is meant to sync your local photos to Google Photos, based on ALBUM_CONFIG_FILE and keywords in exifdata.

ALBUM_CONFIG_FILE format is:
# AlbumName:
#   Keywords: '<reg-exp>'
#   FilePath: '.*'

use --noauth_local_webserver to generate credentials.json if running on a box with no browser

Tested with Python 3.7.4

pip install -r requirements.txt to use version of modules I tested with OR take your chances and run:
pip install --upgrade google-api-python-client oauth2client PyExifTool requests pyyaml

TODO: Write tests
TODO: Finish sync feature, now only upload photos and add-to/create albums but doesn't remove from album
TODO: Some hard-coded values to clean-up
TODO: Fix hack about wrong timezone, -1d or at make it an option

"""

import argparse
import glob
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from datetime import timedelta
from pprint import pformat

import exiftool
import requests
import yaml
from apiclient.discovery import build
from httplib2 import Http
from oauth2client import client
from oauth2client import file
from oauth2client import tools

ALBUM_CONFIG_FILE = 'album-rules.yaml'

logger = logging.getLogger()


class PhotosSync:
    def __init__(self):
        self.albums = []
        self.photos_already_uploaded = set()
        self.google_photos_client = GooglePhotosClient()
        self.google_photos_albums = {}
        self.config = Config()
        self.local_photos = []
        self.local_photos_exif_data = []
        self.photos_to_upload_per_albums = {}
        self.photos_to_upload = set()

    def list_local_photos(self, path):
        logger.debug('Listing local photos in %s ... ' % path)
        local_photos = []
        photo_iter = glob.iglob(path + '/**/*.*', recursive=True)
        ext_re = re.compile('.*(jpg|JPG)')

        for f in photo_iter:
            if ext_re.match(f):
                local_photos.append(f)
        logger.info('%s photos found in %s ... ' % (len(local_photos), path))

        if not local_photos:
            logger.critical('No photos found, exiting ...')
            sys.exit(0)
        self.local_photos = local_photos
        return

    def load_local_photos_exif_data(self):
        logger.info('Retrieving exif data ... ')
        t0 = time.time()
        with exiftool.ExifTool() as et:
            # metadata = et.get_metadata_batch(self.local_photos)
            self.local_photos_exif_data = et.get_tags_batch(["SourceFile", "IPTC:Keywords", "EXIF:DateTimeOriginal", "EXIF:OffsetTimeOriginal"],
                                          self.local_photos)
            logger.debug('%i of %i retrieved successfully' % (len(self.local_photos_exif_data), len(self.local_photos)))
        td = (time.time() - t0)
        logger.info('Done retrieving exif data of {2} photos in {0:.2f}s ({1:.3f}s per photo)'.format(td, (td / len(self.local_photos)), len(self.local_photos_exif_data)))
        return

    def map_local_photos_to_albums(self):
        albums_mapping = self.config.albums_mapping
        logger.debug("albumsMapping: %s" % pformat(albums_mapping))
        photos_to_upload_per_albums = {}
        for album_name in albums_mapping.keys():
            keywords_regex = re.compile(albums_mapping[album_name]['Keywords'])
            filePath_regex = re.compile(albums_mapping[album_name]['FilePath'])
            photos_to_upload_per_albums[album_name] = set()
            for d in self.local_photos_exif_data:
                # In some cases d["IPTC:Keywords"] stores a string and some other, a list (of string).
                if "IPTC:Keywords" in d and \
                        (any(keywords_regex.match(keyword) for keyword in d["IPTC:Keywords"]) or
                         (isinstance(d["IPTC:Keywords"], str) and keywords_regex.match(d["IPTC:Keywords"])))\
                        and filePath_regex.match(d["SourceFile"]):
                    photos_to_upload_per_albums[album_name].add(
                        Photo(filename=d["SourceFile"], creationTime=datetime.strptime(d["EXIF:DateTimeOriginal"], '%Y:%m:%d %H:%M:%S'), keywords=d["IPTC:Keywords"]))

        logger.debug("photosToUploadPerAlbums: %s" % pformat(photos_to_upload_per_albums))
        for album in photos_to_upload_per_albums.keys():
            logger.info("%s photos to upload for album %s" % (len(photos_to_upload_per_albums[album]), album))
        self.photos_to_upload_per_albums = photos_to_upload_per_albums

        # Make a set of all photos to upload
        flatten = lambda l: [item for sublist in l for item in sublist]
        self.photos_to_upload = set(list(flatten(photos_to_upload_per_albums.values())))
        return

    def __list_google_albums(self):
        self.google_photos_albums = self.google_photos_client.list_albums()

    def __list_google_photos(self):
        self.photos_already_uploaded = set()
        for i in self.google_photos_client.list_items():
            self.photos_already_uploaded.add(Photo(
                googleId=i['id'],
                filename=i['filename'],
                googleDescription=i.get('description', None),
                googleMetadata=i['mediaMetadata']))

    def __search_google_photos_for_photos_to_upload_time_range(self):
        self.photos_already_uploaded = set()
        if not self.photos_to_upload:
            return  # In case no photos are to upload, don't query Google Photo API
        oldest_photo_dt, newest_photo_dt = min(self.photos_to_upload).creationTime, max(self.photos_to_upload).creationTime
        oldest_photo_dt = oldest_photo_dt - timedelta(days=1)  # HACK: some pics taken abroad have wrong timezone in exifdata but google photos override with correct timezone.
        logger.info('Listing google photos from %s to %s' % (oldest_photo_dt, newest_photo_dt))
        for i in self.google_photos_client.search_items_by_date_range(oldest_photo_dt, newest_photo_dt):
            self.photos_already_uploaded.add(Photo(
                googleId=i['id'],
                filename=i['filename'],
                googleDescription=i.get('description', None),
                googleMetadata=i['mediaMetadata']))
        logger.debug('Listed google photos: %s' % self.photos_already_uploaded)

    def __copy_google_id_to_photos_to_upload_per_albums(self, from_photos):
        # self.photos_to_upload_per_albums (local) don't have a google_id so get it from self.photos_already_uploaded
        if not self.google_photos_albums:
            self.__list_google_albums()

        for album_name in self.photos_to_upload_per_albums:
            for photo in self.photos_to_upload_per_albums[album_name]:
                try:
                    photo.googleId = next(photo_already_uploaded.googleId for photo_already_uploaded in from_photos if photo_already_uploaded.filename == photo.filename)
                except StopIteration:
                    logger.warning('(Expected if --pretend) Cannot find google_id for %s' % photo)

    def upload_photos(self, pretend=False):
        # self.__list_google_photos()  # This list all google photos  # Used this before too long to list all photos
        self.__search_google_photos_for_photos_to_upload_time_range()  # This list all google photos for time range
        self.__copy_google_id_to_photos_to_upload_per_albums(self.photos_already_uploaded)

        # Only upload photos that are not already on GooglePhotos (using standardized filename as comparator)
        photos_to_upload_not_already_uploaded = self.photos_to_upload - self.photos_already_uploaded
        photos_already_uploaded_to_update = self.photos_already_uploaded & self.photos_to_upload

        if not photos_to_upload_not_already_uploaded:
            logging.info('No photos to upload')
            return 0

        logging.info("%s photos to upload." % len(photos_to_upload_not_already_uploaded))
        logging.info("%s photos to update metadata" % len(photos_already_uploaded_to_update))

        results = self.google_photos_client.upload(photos_to_upload_not_already_uploaded, pretend=pretend)
        logger.debug('results: %s' % results)

        # Copy google_id from photo_just_uploaded in self.photos_to_upload_per_albums so that we can add them to albums.
        # Would be nicer to re-use __copy_google_id_to_photos_to_upload_per_albums but needs to convert results from batchCreateItems API call into set() of Photo(s).
        for album_name in self.photos_to_upload_per_albums:
            for photo in self.photos_to_upload_per_albums[album_name]:
                if not photo.googleId:
                    logger.debug('photo: %s' % photo.filename)
                    try:
                        photo.googleId = next(photo_just_uploaded['mediaItem']["id"] for photo_just_uploaded in results if photo_just_uploaded['mediaItem']['filename'] == photo.filename)
                    except StopIteration:
                        logger.warning('(Expected if --pretend, otherwise means a photo failed to upload) Cannot find google_id for %s' % photo)

    def create_missing_albums(self, pretend=False):
        self.__list_google_albums()
        # Create albums if don't already exist.
        albums_to_create = [a for a in self.config.albums_mapping.keys() if a not in [d['title'] for d in self.google_photos_albums]]

        for album_to_create in albums_to_create:
            self.google_photos_client.create_album(album_to_create, pretend=pretend)

        if albums_to_create:
            self.__list_google_albums()

    def add_photos_to_albums(self, pretend=False):
        for album_name in self.photos_to_upload_per_albums.keys():
            logger.debug('Photos to add to %s album: %s' % (album_name, self.photos_to_upload_per_albums[album_name]))
            logger.info('%s photos to add to %s album' % (len(self.photos_to_upload_per_albums[album_name]), album_name))

            # Get album_id from album_name
            album_id = next(album['id'] for album in self.google_photos_albums if album["title"] == album_name)
            self.google_photos_client.add_items_to_album(self.photos_to_upload_per_albums[album_name], album_id, pretend=pretend)

    # TODO: should also remove photos that don't belong to album anymore relying on photo timestamp
    def sync(self, photos, pretend=False):
        # Only upload photos that are not already on GooglePhotos (using filename as comparator)
        photosToUpload = photos - self.photosAlreadyUploaded
        photosToUpdateMetadata = self.photosAlreadyUploaded & photos

        # ...


class Config:
    def __init__(self):
        logger.debug('Loading Album matching rules from %s' % ALBUM_CONFIG_FILE)
        try:
            with open(ALBUM_CONFIG_FILE, 'r') as opened_file:
                self.albums_mapping = yaml.load(opened_file.read(), Loader=yaml.BaseLoader)
            logger.info('Loaded Album matching rules from %s, albums defined: %s' % (ALBUM_CONFIG_FILE, ', '.join(self.albums_mapping.keys())))
        except yaml.YAMLError as exc:
            logger.critical("Error in Album mapping config file:", exc)
            sys.exit(1)


class GooglePhotosClient:
    def __init__(self):
        api_cred_file = 'python-script-non-web-cred.json'  # This is downloadable from your Google API page
        app_cred_file = 'credentials.json'  # This one gets generated by this script
        scopes = 'https://www.googleapis.com/auth/photoslibrary'
        self.appCredStore = file.Storage(app_cred_file)
        self.appCreds = self.appCredStore.get()

        if not self.appCreds or self.appCreds.invalid:
            flags = tools.argparser.parse_args(args=[])  # tools.run_flow() will call it's own argparse so make it ignore this script's cmd line args
            flow = client.flow_from_clientsecrets(api_cred_file, scopes)
            self.appCreds = tools.run_flow(flow, self.appCredStore, flags)
        self.service = build('photoslibrary', 'v1', http=self.appCreds.authorize(Http()))

    # This will only upload photos that aren't already uploaded.
    def upload(self, photos, batchSize=25, pretend=False):
        UPLOAD_URL = 'https://photoslibrary.googleapis.com/v1/uploads'
        all_results = []

        #  Upload photo and get uploadToken to create item later
        for p in photos:
            if pretend:
                logger.info("Simulating uploading %s ... " % p.filename)
                continue
            else:
                logger.debug("Uploading %s ... " % p.filename)

            UPLOAD_HEADERS = {
                'Authorization': "Bearer " + self.service._http.request.credentials.access_token,
                'Content-Type': 'application/octet-stream',
                'X-Goog-Upload-File-Name': p.filename,
                'X-Goog-Upload-Protocol': "raw",
            }
            with open(p.filepath, 'rb') as opened_file:
                f = opened_file.read()
            t0 = time.time()
            r = requests.post(UPLOAD_URL, data=f, headers=UPLOAD_HEADERS)
            td = (time.time() - t0)
            logger.info('Uploaded {0} in {1:.2f}s '.format(p.filename, td))

            p.uploadToken = r.text

        # Create items from uploadToken
        payload = {"newMediaItems": []}
        for (i, p) in enumerate(photos):
            payload["newMediaItems"].append({
                    "description": "None",
                    "simpleMediaItem": {
                        "uploadToken": p.uploadToken
                    }})
            if len(payload["newMediaItems"]) >= batchSize or i == (len(photos) - 1):
                logger.debug('Creating %s items ...' % len(payload["newMediaItems"]))
                if pretend:
                    logger.info("{0} items creation simulated".format(len(payload["newMediaItems"])))
                    payload["newMediaItems"] = []
                    continue
                t0 = time.time()
                request = self.service.mediaItems().batchCreate(body=payload)
                results = request.execute()
                all_results += results['newMediaItemResults']
                td = (time.time() - t0)
                logger.info('Created {0} items in {1:.2f}s'.format(len(results['newMediaItemResults']), td))
                payload["newMediaItems"] = []

        return all_results

    def create_album(self, albumName, pretend=False):
        payload = {"album": {"title": albumName}}
        logger.info('Creating album: %s' % albumName)
        if not pretend:
            self.service.albums().create(body=payload).execute()
        return

    def add_items_to_album(self, photos, album_id, batchSize=40, pretend=False):
        payload = {"mediaItemIds": []}
        for (i, photo) in enumerate(photos):
            payload["mediaItemIds"].append(photo.googleId)

            if len(payload["mediaItemIds"]) >= batchSize or i == (len(photos) - 1):
                if not pretend:
                    if payload["mediaItemIds"].count(None) > 0:
                        logger.warning('%s items to add to %s have no google_id !' % (payload["mediaItemIds"].count(None), album_id))
                    else:
                        logger.info('All items to add to album have a google_id :-)')

                logger.debug('Adding %s items to album %s ...' % (len(payload["mediaItemIds"]), album_id))

                if pretend:
                    logger.info("Simulating adding %s items to album %s ..." % (len(payload["mediaItemIds"]), album_id))
                    payload["mediaItemIds"] = []
                    continue

                t0 = time.time()
                self.service.albums().batchAddMediaItems(albumId=album_id, body=payload).execute()
                td = (time.time() - t0)
                logger.info('Added {0} items to album in {1:.2f}s ({2}/{3})'.format(len(payload["mediaItemIds"]), td, i+1, len(photos)))
                payload = {"mediaItemIds": []}
        return

    def __search_items(self, field):
        medias = []
        next_page_token = ''
        while True:
            search = {'pageSize': 100,
                      'pageToken': next_page_token}
            search.update(field)
            media_list = self.service.mediaItems().search(body=search).execute()

            if 'mediaItems' not in media_list:
                break

            medias += media_list['mediaItems']
            logger.debug('Got %i more items while searching, total: %i' % (len(media_list['mediaItems']), len(medias)))

            if 'nextPageToken' not in media_list:
                break

            next_page_token = media_list['nextPageToken']

        logger.info('Found %i items while searching google photos' % len(medias))
        return medias

    def search_items_by_album(self, album_id):
        return self.__search_items(field={'albumId': album_id})

    def search_items_by_date_range(self, datetime_from=None, datetime_to=None):
        date_filter = {"filters": {"dateFilter": {
            "ranges": [{
              "startDate": {
                  "year": datetime_from.year,
                  "month": datetime_from.month,
                  "day": datetime_from.day
                },
              "endDate": {
                  "year": datetime_to.year,
                  "month": datetime_to.month,
                  "day": datetime_to.day
                }
            }]}}}
        return self.__search_items(field=date_filter)

    def list_items(self):
        logger.info('Listing all google photos...')
        medias = []
        next_page_token = ''
        while True:
            media_list = self.service.mediaItems().list(pageSize=50, pageToken=next_page_token).execute()

            if 'mediaItems' not in media_list:
                break

            medias += media_list['mediaItems']
            logger.debug('Got %i more items while listing, total: %i' % (len(media_list['mediaItems']), len(medias)))

            if 'nextPageToken' not in media_list:
                break

            next_page_token = media_list['nextPageToken']

        logger.info('Found %i items while listing google photos' % len(medias))
        return medias

    def list_albums(self):
        results = self.service.albums().list(
            pageSize=50, fields="nextPageToken,albums(id,title)").execute()
        return results.get('albums', [])


class Photo:
    def __init__(self, filename, **kwargs):
        self.filepath = filename  # Used only for uploading
        self.filename = re.sub(r'.*/Photos/', '', filename)  # Standardize filename, it's used as identifier / comparator
        self.googleId = kwargs.pop('googleId', None)
        self.googleDescription = kwargs.pop('googleDescription', None)
        self.googleMetadata = kwargs.pop('googleMetadata', None)
        self.creationTime = kwargs.pop('creationTime', None)
        self.keywords = kwargs.pop('keywords', None)
        self.uploadToken = None

    # Not defining __str__ so __repr__ is used
    def __repr__(self):
        return "Photo(filename='{0}', keywords='{1}', creationTime='{2}', gid='{3}')".format(self.filename, self.keywords, self.creationTime, self.googleId)

    # Used for set subtraction
    def __eq__(self, obj):
        return isinstance(obj, Photo) and obj.filename == self.filename

    def __lt__(self, obj):
        return isinstance(obj, Photo) and self.creationTime < obj.creationTime

    def __gt__(self, obj):
        return isinstance(obj, Photo) and self.creationTime > obj.creationTime

    def __hash__(self):
        return hash(self.filename)


def main():
    # Parse command-line options
    parser = argparse.ArgumentParser()
    log_levels = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    log_scope = ('root', 'all')
    parser.add_argument('--log-level', default='INFO', choices=log_levels)
    parser.add_argument('--log-scope', default='root', choices=log_scope)
    parser.add_argument("--path", help="path of photos to sync", required=True)
    parser.add_argument("--pretend", help="dry-run mode", action="store_true")
    args = parser.parse_args()

    # Setup logging
    sh = logging.StreamHandler(sys.stdout)
    #  Filter must be on handler to filter other module logging: http://docs.python.org/library/logging.html#filter-objects
    if args.log_scope == 'root':
        sh.addFilter(logging.Filter(name=args.log_scope))
    formatter = logging.Formatter('[%(levelname)s] [%(name)s] %(message)s')
    sh.setFormatter(formatter)
    sh.setLevel(args.log_level)
    logger.addHandler(sh)
    logger.setLevel(args.log_level)

    # TODO: only add photos to albums that aren't already part of it and remove ones that aren't part of it anymore, for this we need to search items for that album.

    ps = PhotosSync()

    ps.list_local_photos(args.path)

    ps.load_local_photos_exif_data()

    ps.map_local_photos_to_albums()

    ps.upload_photos(pretend=args.pretend)

    ps.create_missing_albums(pretend=args.pretend)

    ps.add_photos_to_albums(pretend=args.pretend)


if __name__ == "__main__":
    main()
