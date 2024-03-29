"""
ALBUM_CONFIG_FILE format is:
# <AlbumName>:
#   FilePath: '.*'
#   KeywordsIncl: '<reg-exp>'
#   KeywordsExcl: '<reg-exp>'

"""

import logging
import yaml

ALBUM_CONFIG_FILE = 'albums.yaml'
# CONTRIBUTOR_NAME is used if shared album have items that are uploaded by other users and need to be excluded from the sync
CONTRIBUTOR_NAME = '<name shown on photos in shared album>'
# Used if exifdata does not contain timezone info. You'll see a warning if this is used.
FALLBACK_TZ = +2

# This is used to shorten file path which is used as photos identifier (it's human readable and does not change when exifdata gets modified).
# Not using full path allows changing photos' basedir and hides full path from Google Photos ("filename" field).
FILE_PATH_SHORTENING_REGEX = r'.*/Photos/'

logger = logging.getLogger()


class Config:
    def __init__(self, album_config_file=ALBUM_CONFIG_FILE):
        logger.debug('Loading Album matching rules from %s' % album_config_file)
        try:
            with open(album_config_file, 'r') as opened_file:
                self.albums_mapping = yaml.load(opened_file.read(), Loader=yaml.BaseLoader)
            logger.info('Loaded Album matching rules from %s, albums defined: %s' % (album_config_file, ', '.join(self.albums_mapping.keys())))
        except yaml.YAMLError as exc:
            logger.critical("Error in Album mapping config file:", exc)
            sys.exit(1)
        except FileNotFoundError as exc:
            logger.warning("'{}' file not found, assuming empty Error in Album mapping config file:", album_config_file)
            self.albums_mapping = {}

