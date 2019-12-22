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

# This is used to shorten file path which is used as photos identifier (it's human readable and does not change when exifdata gets modified).
# Not using full path allows changing photos' basedir and hides full path from Google Photos ("filename" field).
FILE_PATH_SHORTENING_REGEX = r'.*/Photos/'

logger = logging.getLogger()


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
        except FileNotFoundError as exc:
            logger.warning("'{}' file not found, assuming empty Error in Album mapping config file:", ALBUM_CONFIG_FILE)
            self.albums_mapping = {}

