"""Import pinned light-novel metadata/features into the independent catalogue."""
import argparse
import json
from pathlib import Path

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('data/light-novels-v1/catalogue.sqlite'))
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--features', type=Path)
    parser.add_argument('--features-sha256')
    parser.add_argument('--author-authorities', type=Path)
    parser.add_argument('--author-authorities-sha256')
    parser.add_argument('--publisher-series-frames', type=Path)
    parser.add_argument('--publisher-series-frames-sha256')
    parser.add_argument('--publisher-source-root', type=Path)
    args = parser.parse_args()
    if not any((args.metadata, args.features, args.author_authorities, args.publisher_series_frames)):
        parser.error('at least one metadata/features/author-authorities/publisher-series input is required')
    if bool(args.features) != bool(args.features_sha256):
        parser.error('--features and --features-sha256 are required together')
    if bool(args.author_authorities) != bool(args.author_authorities_sha256):
        parser.error('--author-authorities and --author-authorities-sha256 are required together')
    if any((args.publisher_series_frames, args.publisher_series_frames_sha256, args.publisher_source_root)) and not all((args.publisher_series_frames, args.publisher_series_frames_sha256, args.publisher_source_root)):
        parser.error('--publisher-series-frames, --publisher-series-frames-sha256, and --publisher-source-root are required together')
    catalogue = LightNovelCatalogue(args.database)
    result = {}
    if args.metadata:
        result['metadata'] = catalogue.import_metadata(args.metadata)
    if args.features:
        result['features'] = catalogue.import_features(args.features, args.features_sha256)
    if args.author_authorities:
        result['edition_series_memberships'] = catalogue.resolve_edition_series_memberships(args.author_authorities, args.author_authorities_sha256)
    if args.publisher_series_frames:
        result['ndl_series'] = catalogue.import_ndl_series_from_publishers(args.publisher_series_frames,
            args.publisher_series_frames_sha256, source_root=args.publisher_source_root)
    result['stats'] = catalogue.catalogue_stats()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
