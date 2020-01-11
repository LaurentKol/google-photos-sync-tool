from apiclient.discovery import build
from googleapiclient import errors
from httplib2 import Http
import logging
from oauth2client import client
from oauth2client import file
from oauth2client import tools
import requests
import time

logger = logging.getLogger()
API_MAX_RETRIES = 3


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
    def upload(self, photos, batch_size=25, pretend=False):
        UPLOAD_URL = 'https://photoslibrary.googleapis.com/v1/uploads'
        all_results = []

        #  Upload photo and get uploadToken to create item later
        for p in photos:
            if pretend:
                logger.info("Simulating uploading %s ... " % p.short_file_path)
                continue
            else:
                logger.debug("Uploading %s ... " % p.short_file_path)

            UPLOAD_HEADERS = {
                'Authorization': "Bearer " + self.service._http.request.credentials.access_token,
                'Content-Type': 'application/octet-stream',
                'X-Goog-Upload-File-Name': p.short_file_path,
                'X-Goog-Upload-Protocol': "raw",
            }
            with open(p.file_path, 'rb') as opened_file:
                f = opened_file.read()
            t0 = time.time()
            r = requests.post(UPLOAD_URL, data=f, headers=UPLOAD_HEADERS)
            td = (time.time() - t0)
            logger.info('Uploaded {0} in {1:.2f}s '.format(p.short_file_path, td))

            p.uploadToken = r.text

        # Create items from uploadToken
        payload = {"newMediaItems": []}
        for (i, p) in enumerate(photos):
            payload["newMediaItems"].append({
                    "description": "None",
                    "simpleMediaItem": {
                        "uploadToken": p.uploadToken
                    }})
            if len(payload["newMediaItems"]) >= batch_size or i == (len(photos) - 1):
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

    def add_items_to_album(self, photos, album_id, batch_size=40, pretend=False):
        payload = {"mediaItemIds": []}
        for (i, photo) in enumerate(photos):
            payload["mediaItemIds"].append(photo.googleId)

            if len(payload["mediaItemIds"]) >= batch_size or i == (len(photos) - 1):
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

                retries = 0
                while retries <= API_MAX_RETRIES:
                    try:
                        self.service.albums().batchAddMediaItems(albumId=album_id, body=payload).execute()
                        break
                    except errors.HttpError as e:
                        logger.warn(f'HttpError while querying {e.uri}, err:{e.content}, retries left:{(API_MAX_RETRIES - retries)}')
                        retries += 1

                td = (time.time() - t0)
                logger.info('Added {0} items to album in {1:.2f}s ({2}/{3})'.format(len(payload["mediaItemIds"]), td, i+1, len(photos)))
                payload = {"mediaItemIds": []}
        return

    def remove_items_from_album(self, photos, album_id, batch_size=40, pretend=False):
        payload = {"mediaItemIds": []}
        for (i, photo) in enumerate(photos):
            payload["mediaItemIds"].append(photo.googleId)

            if len(payload["mediaItemIds"]) >= batch_size or i == (len(photos) - 1):
                if not pretend:
                    if payload["mediaItemIds"].count(None) > 0:
                        logger.warning('%s items to remove from %s have no google_id !' % (payload["mediaItemIds"].count(None), album_id))
                    else:
                        logger.info('All items to remove from album have a google_id :-)')

                logger.debug('Removing %s items from album %s ...' % (len(payload["mediaItemIds"]), album_id))

                if pretend:
                    logger.info("Simulating removing %s items from album %s ..." % (len(payload["mediaItemIds"]), album_id))
                    payload["mediaItemIds"] = []
                    continue

                t0 = time.time()
                self.service.albums().batchRemoveMediaItems(albumId=album_id, body=payload).execute()
                td = (time.time() - t0)
                logger.info('Removed {0} items from album in {1:.2f}s ({2}/{3})'.format(len(payload["mediaItemIds"]), td, i+1, len(photos)))
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

    # Google Photos API doesn't support conjunction of album and time range filters
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
