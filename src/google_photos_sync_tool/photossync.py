"""

This tool is meant to sync your local photos to Google Photos, based on ALBUM_CONFIG_FILE and keywords in exifdata.

use --noauth_local_webserver to generate credentials.json if running on a box with no browser

Tested with Python 3.7.4

TODO: Write more tests
TODO: Add option to force listing all photos instead of relying on time range (to workaround possible timezone issue)
TODO: Fix hack about wrong timezone, -1d or at make it an option
TODO: Finish sync feature, now only upload photos and add-to/create albums but doesn't remove from album
TODO: Some hard-coded values to clean-up
TODO: Rewrite upload, add_items_to_album, remove_items_from_album from GooglePhotosClient to share batching logic
TODO: Implement retries on API call failure
TODO: Improve timezone detection, e.g: DJI store GPS location but not GPS time, to convert into UTC need to resolve TZ from location, maybe with https://pypi.org/project/timezonefinder/
"""

import glob
import logging
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pprint import pformat

import exiftool

from google_photos_sync_tool.config import FILE_PATH_SHORTENING_REGEX
from google_photos_sync_tool.photo import Photo
from google_photos_sync_tool.googlephotosclient import GooglePhotosClient

logger = logging.getLogger()


class PhotosSync:
    def __init__(self):
        self.albums = []
        self.photos_already_uploaded = set()
        self.google_photos_client = GooglePhotosClient()
        self.google_photos_albums = {}
        self.local_photos = []
        self.local_photos_exif_data = []
        self.photos_to_upload_per_albums = {}
        self.photos_to_upload = set()

    def list_local_photos(self, path):
        logger.debug('Listing local photos in %s ... ' % path)
        local_photos = []
        photo_iter = glob.iglob(path + '/**/*.*', recursive=True)
        ext_re = re.compile('.*(jpg|JPG)')  # Make this configurable

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
            self.local_photos_exif_data = et.get_tags_batch(
                ["SourceFile",
                "IPTC:Keywords",
                "EXIF:DateTimeOriginal",
                "EXIF:SubSecDateTimeOriginal",
                "EXIF:OffsetTimeOriginal"],
                self.local_photos)
            logger.debug('%i of %i retrieved successfully' % (len(self.local_photos_exif_data), len(self.local_photos)))
        td = (time.time() - t0)
        logger.info('Done retrieving exif data of {2} photos in {0:.2f}s ({1:.3f}s per photo)'.format(td, (td / len(self.local_photos)), len(self.local_photos_exif_data)))
        return

    @staticmethod
    def __normalize_to_list(obj):
        return obj if isinstance(obj, list) else [obj]

    # Check if every regexp match at least one exif keyword.
    @staticmethod
    def __match_all(keywords, regexps):
        for regexp in regexps:
            if not any(regexp.match(kw) for kw in keywords):
                return False
        return True

    def match_local_photos_to_albums(self, config):
        logger.debug("albumsMapping: %s" % pformat(config.albums_mapping))
        photos_to_upload_per_albums = {}
        for album_name in config.albums_mapping.keys():
            album_mapping = config.albums_mapping[album_name]
            regexps = {}
            if 'KeywordsIncl' in album_mapping:
                regexps['KeywordsIncl'] = [re.compile(regex) for regex in self.__normalize_to_list(album_mapping['KeywordsIncl'])]
            if 'KeywordsExcl' in album_mapping:
                regexps['KeywordsExcl'] = [re.compile(regex) for regex in self.__normalize_to_list(album_mapping['KeywordsExcl'])]
            if 'FilePath' in album_mapping:
                regexps['FilePath'] = [re.compile(regex) for regex in self.__normalize_to_list(album_mapping['FilePath'])]
            else:
                logger.critical(f"No 'FilePath' defined for album '{album_name}', exiting ...")
                sys.exit(1)

            # For each photo, check if belongs to album.
            photos_to_upload_per_albums[album_name] = set()
            previous_photo_tz = None
            for exif_data in self.local_photos_exif_data:
                # Go to next photo if its file path doesn't match albums_mapping defined in ALBUM_CONFIG_FILE.
                photo_file_path = re.sub(FILE_PATH_SHORTENING_REGEX, '', exif_data["SourceFile"])
                if not self.__match_all([photo_file_path], regexps['FilePath']):
                    continue

                # Allow photo to have no Exifdata, in case only want to match against FilePath
                if "IPTC:Keywords" not in exif_data:
                    exif_data["IPTC:Keywords"] = ''

                # Normalize into a list because d["IPTC:Keywords"] stores a string if single kw and a list of string if multiple kw
                exif_kws = [exif_data["IPTC:Keywords"]] if isinstance(exif_data["IPTC:Keywords"], str) else exif_data["IPTC:Keywords"]

                if 'KeywordsIncl' in regexps and not self.__match_all(exif_kws, regexps['KeywordsIncl']):
                    continue
                if 'KeywordsExcl' in regexps and self.__match_all(exif_kws, regexps['KeywordsExcl']):
                    continue

                # If we reached here, this photo belongs to album_name.
                # First try to determine UTC time.

                # I think this is fixed in 3.7, to check
                def __tz_normalizer(dt_with_tz):
                    if ":" == dt_with_tz[-3:-2]:
                        return dt_with_tz[:-3]+dt_with_tz[-2:]
                    else:
                        return dt_with_tz

                if 'SubSecDateTimeOriginal' in exif_data:
                    creation_time = datetime.strptime(__tz_normalizer(exif_data["EXIF:SubSecDateTimeOriginal"]), '%Y:%m:%d %H:%M:%S%z')
                    # In case next photo has no TZ info, fallback to previous photo's TZ (not great but better than assuming UTC)
                    previous_photo_tz = creation_time.tzinfo
                elif 'EXIF:DateTimeOriginal' and 'EXIF:OffsetTimeOriginal' in exif_data:
                    creation_time = datetime.strptime(exif_data["EXIF:DateTimeOriginal"] + __tz_normalizer(exif_data["EXIF:OffsetTimeOriginal"]), '%Y:%m:%d %H:%M:%S%z')
                    previous_photo_tz = creation_time.tzinfo
                elif 'EXIF:DateTimeOriginal' in exif_data and previous_photo_tz:
                    creation_time = datetime.strptime(exif_data["EXIF:DateTimeOriginal"], '%Y:%m:%d %H:%M:%S').replace(tzinfo=previous_photo_tz)
                    logger.warn(f"Could not find timezone information for {exif_data['SourceFile']}, defaulting to previous photos ({str(previous_photo_tz)}), assuming creation_time: {creation_time}")
                elif 'EXIF:DateTimeOriginal' in exif_data:
                    creation_time = datetime.strptime(exif_data["EXIF:DateTimeOriginal"], '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=+2)))
                    logger.warn(f"Could not find timezone information for {exif_data['SourceFile']} assuming +2 JST (most likely incorrect), assuming creation_time: {creation_time}")
                    logger.warn(f"You can manually set timezone with: exiftool -preserve -overwrite_original -if 'not $offsettimeoriginal' -offsettimeoriginal='+XX:00' {exif_data['SourceFile']}")
                else:
                    logger.critical(f"{exif_data['SourceFile']} has no EXIF:DateTimeOriginal tag. Rather not continue without reasonably reliable way to determine photo's creation time. Exiting ...")
                    sys.exit(1)

                photos_to_upload_per_albums[album_name].add(
                    Photo(file_path=exif_data["SourceFile"],
                          creationTime=creation_time,
                          keywords=exif_data["IPTC:Keywords"]))

        logger.debug("photosToUploadPerAlbums: %s" % pformat(photos_to_upload_per_albums))
        for album in photos_to_upload_per_albums.keys():
            logger.info("%s photos to add to album %s" % (len(photos_to_upload_per_albums[album]), album))
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
                short_file_path=i['filename'],
                googleDescription=i.get('description', None),
                googleMetadata=i['mediaMetadata']))

    def __search_google_photos_for_photos_to_upload_time_range(self):
        self.photos_already_uploaded = set()
        if not self.photos_to_upload:
            return  # In case no photos are to upload, don't query Google Photo API
        oldest_photo, newest_photo = min(self.photos_to_upload), max(self.photos_to_upload)
        #oldest_photo = oldest_photo.creationTime - timedelta(days=1)  # HACK: some pics taken abroad have wrong timezone in exifdata but google photos override with correct timezone.
        logger.info('Listing google photos from %s to %s' % (oldest_photo.creationTime, newest_photo.creationTime))
        for i in self.google_photos_client.search_items_by_date_range(oldest_photo.creationTime, newest_photo.creationTime):
            self.photos_already_uploaded.add(Photo(
                googleId=i['id'],
                short_file_path=i['filename'],
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
                    photo.googleId = next(photo_already_uploaded.googleId for photo_already_uploaded in from_photos if photo_already_uploaded.short_file_path == photo.short_file_path)
                except StopIteration:
                    logger.debug('Cannot find google_id for %s it is either not uploaded yet or we are running with --pretend' % photo)

    def upload_photos(self, pretend=False):
        #self.__list_google_photos()  # This list all google photos  # Used this before, but it's too long to list all google photos
        self.__search_google_photos_for_photos_to_upload_time_range()  # This list all google photos for time range
        self.__copy_google_id_to_photos_to_upload_per_albums(self.photos_already_uploaded)

        # Only upload photos that are not already on GooglePhotos (using short_file_path as comparator)
        photos_to_upload_not_already_uploaded = self.photos_to_upload - self.photos_already_uploaded
        photos_already_uploaded_to_update = self.photos_already_uploaded & self.photos_to_upload

        if not photos_to_upload_not_already_uploaded:
            logging.info('No photos to upload, either no photos match albums mapping or all the ones that do are already uploaded.')
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
                    logger.debug('photo: %s' % photo.short_file_path)
                    try:
                        photo.googleId = next(photo_just_uploaded['mediaItem']["id"] for photo_just_uploaded in results if photo_just_uploaded['mediaItem']['filename'] == photo.short_file_path)
                    except StopIteration:
                        if not pretend:
                            logger.warning('Cannot find google_id for %s, looks like we failed to upload it.' % photo)

    def create_missing_albums(self, config, pretend=False):
        self.__list_google_albums()
        # Create albums if don't already exist.
        albums_to_create = [a for a in config.albums_mapping.keys() if a not in [d['title'] for d in self.google_photos_albums]]

        for album_to_create in albums_to_create:
            self.google_photos_client.create_album(album_to_create, pretend=pretend)

        if albums_to_create:
            self.__list_google_albums()

    def __get_album_id(self, album_name, pretend):
        # Get album_id from album_name, this can fail if --pretend and album isn't created yet
        try:
            album_id = next(album['id'] for album in self.google_photos_albums if album["title"] == album_name)
        except StopIteration:
            if pretend:
                logger.info(f"(Expected if --pretend, otherwise means album creation failed) Album '{album_name}' does not exist yet")
            else:
                logger.critical(f"Cannot find album '{album_name}' on Google Photos!")
            return None
        return album_id

    def add_photos_to_albums(self, pretend=False):
        for album_name in self.photos_to_upload_per_albums.keys():
            logger.debug('Photos to add to %s album: %s' % (album_name, self.photos_to_upload_per_albums[album_name]))
            logger.info('%s photos to add to %s album' % (len(self.photos_to_upload_per_albums[album_name]), album_name))

            album_id = self.__get_album_id(album_name, pretend)
            self.google_photos_client.add_items_to_album(self.photos_to_upload_per_albums[album_name], album_id, pretend=pretend)

    def remove_photos_from_albums(self, pretend=False):
        if not self.google_photos_albums:
            self.__list_google_albums()

        for album_name in self.photos_to_upload_per_albums.keys():
            album_id = self.__get_album_id(album_name, pretend)
            logger.debug('Album %s is %s' % (album_name, album_id))

            local_photos_in_album = self.photos_to_upload_per_albums[album_name]
            if not local_photos_in_album:
                logger.info(f"There are no local photos that should be in {album_name}, skipping ...")
                continue

            oldest_photo, newest_photo = min(local_photos_in_album), max(local_photos_in_album)
            # oldest_photo = oldest_photo.creationTime - timedelta(days=1)  # HACK: some pics taken abroad have wrong timezone in exifdata but google photos override with correct timezone.
            logger.debug(f"Oldest local photo in matching album config was taken at {oldest_photo.creationTime} ({oldest_photo.short_file_path}) and newest at {newest_photo.creationTime} ({newest_photo.short_file_path}).")

            photos_in_album = set()
            for i in self.google_photos_client.search_items_by_album(album_id):
                try:
                    photo_ct = datetime.strptime(re.sub(r'\.0*([0-9]{0,6})[0-9]*Z$', '.\\1Z', i['mediaMetadata']['creationTime']), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        photo_ct = datetime.strptime(i['mediaMetadata']['creationTime'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    except ValueError:
                        logger.error(f"Unknown time format '{i['mediaMetadata']['creationTime']}' of {i}, skipping ...")
                        continue

                logger.debug(f"Looking at remote photo {i['filename']} taken at '{photo_ct}' (raw_ct:{i['mediaMetadata']['creationTime']}) if between {oldest_photo.creationTime} and {newest_photo.creationTime}")
                if oldest_photo.creationTime <= photo_ct <= newest_photo.creationTime:
                    photos_in_album.add(Photo(
                        googleId=i['id'],
                        short_file_path=i['filename'],
                        googleDescription=i.get('description', None),
                        googleMetadata=i['mediaMetadata']))

            logger.debug('in-google-album vs in-local-album: %s VS %s' % (photos_in_album, local_photos_in_album))
            photos_to_remove_from_album = photos_in_album - local_photos_in_album
            logger.info(f'Photos to remove from {album_name}: {photos_to_remove_from_album}')
            self.google_photos_client.remove_items_from_album(photos_to_remove_from_album, album_id, pretend=pretend)

    def sync(self, photos, pretend=False):
        # Only upload photos that are not already on GooglePhotos (using short_file_path as comparator)
        #photosToUpload = photos - self.photosAlreadyUploaded
        #photosToUpdateMetadata = self.photosAlreadyUploaded & photos

        # ...
        pass



