from datetime import datetime
import re

from google_photos_sync_tool.config import FILE_PATH_SHORTENING_REGEX


class Photo:
    def __init__(self, file_path=None, short_file_path=None, **kwargs):
        if file_path:
            self.file_path = file_path  # Used only for uploading
            self.short_file_path = re.sub(FILE_PATH_SHORTENING_REGEX, '', file_path)  # Standardize filename, it's used as identifier / comparator
        else:
            self.short_file_path = short_file_path
        self.googleId = kwargs.pop('googleId', None)
        self.googleDescription = kwargs.pop('googleDescription', None)
        self.googleMetadata = kwargs.pop('googleMetadata', None)
        creation_time = kwargs.pop('creationTime', None)
        if isinstance(creation_time, str):
            self.creationTime = datetime.strptime(creation_time, '%Y-%m-%d %H:%M:%S')
        else:
            self.creationTime = creation_time
        self.keywords = kwargs.pop('keywords', None)
        self.uploadToken = None

    # Not defining __str__ so __repr__ is used
    def __repr__(self):
        return "Photo(short_file_path='{0}', keywords='{1}', creationTime='{2}', gid='{3}')".format(self.short_file_path, self.keywords, self.creationTime, self.googleId)

    # Used for set subtraction
    def __eq__(self, obj):
        return isinstance(obj, Photo) and obj.short_file_path == self.short_file_path

    def __lt__(self, obj):
        return isinstance(obj, Photo) and self.creationTime < obj.creationTime

    def __gt__(self, obj):
        return isinstance(obj, Photo) and self.creationTime > obj.creationTime

    def __hash__(self):
        return hash(self.short_file_path)
