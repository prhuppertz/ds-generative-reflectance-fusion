"""
Runs loading of MODIS raw scenes, merge their bands into single raster
and reprojects them on same CRS

Usage: stack_and_reproject_modis.py --root=<raw_files_directory> --o=<output_directory> --scenes=<path_to_scenes_list>

Options:
  -h --help                            Show help.
  --version                            Show version.
  --root=<raw_files_directory>         Directory of raw MODIS files
  --o=<output_directory>               Output directory
  --scenes=<path_to_scenes_list>       Path to file listing MODIS scenes to be loaded
"""
import os
import sys
from docopt import docopt
import logging
import yaml
from progress.bar import Bar
import rasterio


base_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../../..")
sys.path.append(base_dir)

from src.notsotoygeneration.io import readers, writers
from src.notsotoygeneration.preprocessing.utils import reproject_raster

CRS = rasterio.crs.CRS.from_epsg(4326)


def main(args):
    # Instantiate reader and writer
    bands_reader = readers.MODISBandReader(root=args['--root'])
    scene_writer = writers.MODISSceneWriter(root=args['--o'])

    # Load scenes specification file
    with open(args['--scenes'], 'r') as f:
        scenes_specs = yaml.safe_load(f)

    # Run loading, merging of bands and reprojection
    logging.info(f"Merging bands {scenes_specs['bands']} of MODIS and reprojecting on CRS {CRS}")
    load_stack_and_reproject_scenes(reader=bands_reader,
                                    writer=scene_writer,
                                    scenes_specs=scenes_specs)


def load_stack_and_reproject_scenes(reader, writer, scenes_specs):
    """Loads scene bands rasters, stacks them together into single multiband
    raster and reprojects them at CRS global variable

    Args:
        reader (BandReader): scene band reading utility
        writer (SceneWriter): scene writing utility
        scenes_specs (dict): specification of files to load coordinates, dates and bands
    """
    # Extract list of bands to load
    bands = scenes_specs['bands']

    # Extract list of quality map bands to load
    quality_maps = scenes_specs['quality_maps']

    for coordinate in scenes_specs['coordinates']:
        coordinate_key = reader._format_location_directory(coordinate=coordinate)
        bar = Bar(f"Merging and reprojecting | MODIS Coordinate {coordinate}", max=len(scenes_specs[coordinate_key]['dates']))
        for date in scenes_specs[coordinate_key]['dates']:
            # Load multiband raster
            raster = reader.open(coordinate=coordinate,
                                 date=date,
                                 bands=bands)

            # Load QA raster
            qa_raster = reader.open(coordinate=coordinate,
                                    date=date,
                                    bands=quality_maps)

            # Reproject raster on specified CRS
            reprojected_img, reprojected_meta = reproject_raster(raster=raster, crs=CRS)
            reprojected_qa_img, reprojected_qa_meta = reproject_raster(raster=qa_raster, crs=CRS)

            # Write new raster according to coordinate and date
            with writer(meta=reprojected_meta, coordinate=coordinate, date=date) as reprojected_raster:
                reprojected_raster.write(reprojected_img)

            with writer(meta=reprojected_qa_meta, coordinate=coordinate, date=date, is_quality_map=True) as reprojected_raster:
                reprojected_raster.write(reprojected_qa_img)
            bar.next()


if __name__ == "__main__":
    # Read input args
    args = docopt(__doc__)

    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logging.info(f'arguments: {args}')

    # Run generation
    main(args)