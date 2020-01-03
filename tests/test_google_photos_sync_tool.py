# pytest tests/test_google_photos_sync_tool.py

from mock import patch
import pytest

from google_photos_sync_tool.config import Config
from google_photos_sync_tool.photossync import PhotosSync
from google_photos_sync_tool.photo import Photo


class TestPhotosSync(object):
    testdata = [
        ( 8),
    ]

    @pytest.fixture
    def ps(self):
        ps = PhotosSync()
        ps.list_local_photos("tests/data")
        ps.load_local_photos_exif_data()
        config = Config("tests/data/albums.yaml")
        ps.match_local_photos_to_albums(config)
        return ps

    nb_files = [8]
    @pytest.mark.parametrize("expected", nb_files)
    def test_list_local_photos(self, ps, expected):
        assert len(ps.local_photos) == expected

    nb_files_with_exif_data = [8]
    @pytest.mark.parametrize("expected", nb_files_with_exif_data)
    def test_local_photos_exif_data(self, ps, expected):
        assert len(ps.local_photos_exif_data) == expected

    photos_per_albums_expected = [{
        'BlueOrGreenOrRedOrYellowButNotFriendsNorFamily': {
            Photo(file_path='tests/data/kw-green.jpg', keywords='green', creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-red.jpg', keywords='red', creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-yellow.jpg', keywords='yellow', creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-blue.jpg', keywords='blue', creationTime='2019-04-09 11:12:51', gid=None)},
        'Green': {
            Photo(file_path='tests/data/kw-green.jpg', keywords='green', creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-green-family.jpg', keywords=['family', 'green'], creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-green-family-friends.jpg', keywords=['friends', 'family', 'green'], creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-green-friends.jpg', keywords=['friends', 'green'], creationTime='2019-04-09 11:12:51', gid=None)},
        'GreenAndFriends': {
            Photo(file_path='tests/data/kw-green-family-friends.jpg', keywords=['friends', 'family', 'green'], creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-green-friends.jpg', keywords=['friends', 'green'], creationTime='2019-04-09 11:12:51', gid=None)},
        'GreenButNotFriends': {
            Photo(file_path='tests/data/kw-green.jpg', keywords='green', creationTime='2019-04-09 11:12:51', gid=None),
            Photo(file_path='tests/data/kw-green-family.jpg', keywords=['family', 'green'], creationTime='2019-04-09 11:12:51', gid=None)},
        'GreenOrYellowButFilePathFilter': {
            Photo(short_file_path='tests/data/kw-yellow.jpg', keywords='yellow', creationTime='2019-04-09 11:12:51', gid=None)}
    }]

    # Also compare keywords and creationTime for Photo object equality
    def photo_eq(self, obj):
        return isinstance(obj, Photo) and \
               obj.short_file_path == self.short_file_path and \
               obj.keywords == self.keywords and \
               obj.creationTime == self.creationTime

    @patch("google_photos_sync_tool.photo.Photo.__eq__", photo_eq)
    @pytest.mark.parametrize("expected", photos_per_albums_expected)
    def test_match_local_photos_to_albums(self, ps, expected):
        assert ps.photos_to_upload_per_albums == expected
