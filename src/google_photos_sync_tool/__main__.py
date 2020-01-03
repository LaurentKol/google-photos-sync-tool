import argparse
import logging
import sys
import textwrap

from google_photos_sync_tool.photossync import PhotosSync
from google_photos_sync_tool.config import Config, ALBUM_CONFIG_FILE, FILE_PATH_SHORTENING_REGEX

logger = logging.getLogger()


def main():
    # Parse command-line options
    description = f"""
    This tool upload your photos (local files) to Google Photos if they match album mapping defined in '{ALBUM_CONFIG_FILE}'. 
    
    For a photo to match an album mapping, it must satisfy all following conditions:
    - Its short file path (full path trimmed by '{FILE_PATH_SHORTENING_REGEX}') must match 'FilePath' defined in '{ALBUM_CONFIG_FILE}'. 
    - At least one 'Keywords' from Exif data must match 'KeywordsIncl' if defined in '{ALBUM_CONFIG_FILE}'.
    - None of the 'Keywords' from Exif data must match 'KeywordsExcl' if defined in '{ALBUM_CONFIG_FILE}'.

    This tool does not store state between execution so in order to be able to remove photos from albums without scanning all photos, 
    it assumes you pass all photos for the range between the older and newest photos via the --path argument, 
    if the album on Google Photos contains extra photos for that time range, it will remove them, 
    this tool does not delete photos from Google Photos, only upload and add/remove from albums.
    """
    parser = argparse.ArgumentParser(description=textwrap.dedent(description), formatter_class=argparse.RawDescriptionHelpFormatter)
    log_levels = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    log_scope = ('root', 'all')
    config = Config()
    albums = config.albums_mapping.keys()

    parser.add_argument('--log-level', default='INFO', choices=log_levels)
    parser.add_argument('--log-scope', help="'root' only shows this script's log, 'all' shows logs from libraries, useful for debugging", default='root', choices=log_scope)
    parser.add_argument("--pretend", help="Dry-run mode, do not do anything, just simulate.", action="store_true")

    subparsers = parser.add_subparsers(title="actions available", dest="action")  # would add 'required=True' but breaks for python 3.6

    parser_add_to_albums = subparsers.add_parser('add-to-albums', help='Upload new photos that match any albums mapping and add them to Google Photos albums that they match.')
    parser_add_to_albums.add_argument("--path", help="path of photos to add", required=True)
    parser_add_to_albums.add_argument("--album", help="album to proceed, can be specified multiple times, if omitted assume all albums", action='append', choices=albums)

    parser_remove_from_albums = subparsers.add_parser('remove-from-albums', help="Remove photos from Google Photos albums that do not match album mapping anymore. Only photos that are in time range of oldest/newest photos specified by --path will be remove from albums. This does not delete photos from Google Photos")
    parser_remove_from_albums.add_argument("--path", help="path of photos to scan", required=True)
    parser_remove_from_albums.add_argument("--album", help="album to proceed, can be specified multiple times, if omitted assume all albums", action='append', choices=albums)

    parser_sync_to_albums = subparsers.add_parser('sync-to-albums', help='Upload new photos, add/remove them from Google Photos albums for photos that do not match rule for the time range of the oldest/newest')
    parser_sync_to_albums.add_argument("--path", help="path of photos to scan", required=True)
    parser_sync_to_albums.add_argument("--album", help="album to proceed, can be specified multiple times, if omitted assume all albums", action='append', choices=albums)

    subparsers.add_parser('create-missing-albums', help='Create albums defined in Config that are missing on Google Photos, do not add any photos to it.')
    subparsers.add_parser('validate-albums-mapping', help=f"Validate albums mapping from '{ALBUM_CONFIG_FILE}'.")

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

    def __filter_albums():
        if 'album' in args and args.album:
            ignored_albums = set(config.albums_mapping.keys()) - set(args.album)
            for ignored_album in ignored_albums:
                del config.albums_mapping[ignored_album]
        logger.info(config.albums_mapping)

    if args.action == 'add-to-albums':
        __filter_albums()
        ps = PhotosSync()
        ps.list_local_photos(args.path)
        ps.load_local_photos_exif_data()
        ps.match_local_photos_to_albums(config)
        ps.upload_photos(pretend=args.pretend)
        ps.create_missing_albums(config, pretend=args.pretend)
        ps.add_photos_to_albums(pretend=args.pretend)
    elif args.action == 'remove-from-albums':
        __filter_albums()
        ps = PhotosSync()
        ps.list_local_photos(args.path)
        ps.load_local_photos_exif_data()
        ps.match_local_photos_to_albums(config)
        ps.remove_photos_from_albums(pretend=args.pretend)
    elif args.action == 'sync-to-albums':
        print('Not implemented yet')
    elif args.action == 'create-missing-albums':
        __filter_albums()
        ps = PhotosSync()
        ps.create_missing_albums(config, pretend=args.pretend)
    elif args.action == 'validate-albums-mapping':
        is_config_okay = True
        supported_fields = {'FilePath', 'KeywordsIncl', 'KeywordsExcl'}
        for album_name in config.albums_mapping:
            if 'FilePath' not in config.albums_mapping[album_name].keys():
                logger.critical(f"'{album_name}' is missing required field 'FilePath'.")
                is_config_okay = False
            unsupported_fields = set(config.albums_mapping[album_name].keys()) - supported_fields
            if unsupported_fields:
                logger.critical(f"'{album_name}' has unsupported fields: {', '.join(unsupported_fields)}. Only {', '.join(supported_fields)} are allowed.")
                is_config_okay = False
        if is_config_okay:
            logger.info('Album mapping is valid :-)')
        else:
            logger.critical(f"Edit '{ALBUM_CONFIG_FILE}' and try again.")
    else:
        parser.print_help()


if __name__ == '__main__':
    sys.exit(main())
