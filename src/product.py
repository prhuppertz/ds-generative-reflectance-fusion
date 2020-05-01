import os
from PIL import Image
import numpy as np
import h5py
import random
from torch.utils.data import Dataset
from progress.bar import Bar
from src.utils import setseed, mkdir, save_json, load_json


class Product(dict):
    """Plain product class, composed of a background image and multiple digits

    > Registers digits as a dictionnary {idx: (location_on_bg, digit)}
    > Proposes fully random and grid based patching strategy for digits
    > Generates view of patched image on the fly

    - 'random' mode : each digit location is computed as the product height and
        width scaled by the specified random distribution. Better used with
        distribution valued in [0, 1]
    - 'grid' mode : a regular grid perturbed by the specifed random distribution
        is used to determine digits locations. Better used with centered distributions

    Args:
        size (tuple[int]): (width, height) for background
        horizon (int): number of time steps to generate
        nbands (int): number of bands of the product
        mode (str): patching strategy in {'random', 'grid'}
        grid_size (tuple[int]): grid cells dimensions as (width, height)
        color (int, tuple[int]): color value for background (0-255) according to mode
        digit_transform (callable): geometric transformation to apply digits when patching
        rdm_dist (callable): numpy random distribution to use for randomization
        seed (int): random seed
        digits (dict): hand made dict formatted as {idx: (location, digit)}
    """
    __mode__ = ['random', 'grid']

    def __init__(self, size, horizon=None, nbands=1, mode='random', grid_size=None,
                 color=0, digit_transform=None, rdm_dist=np.random.rand,
                 seed=None, digits={}):
        super(Product, self).__init__(digits)
        self._size = size
        self._nbands = nbands
        self._horizon = horizon
        self._mode = mode
        self._bg = Image.new(size=size, color=color, mode='L')
        self._digit_transform = digit_transform
        self._rdm_dist = rdm_dist
        self._seed = seed

        assert mode in Product.__mode__, f"Invalid mode, must be in {Product.__mode__}"
        if mode == 'grid':
            self._grid_size = grid_size
            self._build_grid(seed=seed)

    @setseed('numpy', 'random')
    def _build_grid(self, seed=None):
        """Builds grid anchors location list

        A public self.grid attribute is created, containing all available grid
            patching locations
        Private self._shuffled_grid attribute is rather used when patching to
            favor scattered patching location when nb of digits < nb locations

        Args:
            seed (int): random seed (default: None)
        """
        # Generate (nb_anchor, 2) array with possible patching locations
        x = np.arange(0, self.size[0], self.grid_size[0])
        y = np.arange(0, self.size[1], self.grid_size[1])
        grid = np.dstack(np.meshgrid(x, y)).reshape(-1, 2)

        # Randomly perturbate grid to avoid over-regular patterns
        eps = self.rdm_dist(*grid.shape).astype(int)
        grid += eps

        # Record ordered grid locations as public attribute
        grid_locs = list(map(tuple, grid))
        self._grid = grid_locs[:]

        # Record shuffled grid locations as private attribute
        random.shuffle(grid_locs)
        self._shuffled_grid = iter(grid_locs)

    def _assert_compatible(self, digit):
        """Ensure digit is compatible with product verifying it has :
            - Same number of bands / dimensionality
            - Same or greater horizon
        Args:
            digit (Digit)
        """
        # Verify matching number of bands
        assert digit.ndim == self.nbands, f"""Trying to add {digit.ndim}-dim digit
            while product is {self.nbands}-dim"""

        # Verify time serie horizon at least equals produc horizon
        if digit.time_serie is not None:
            assert digit.time_serie.horizon >= self.horizon, \
                f"""Digit has {digit.time_serie.horizon} horizon while product has
                 a {self.horizon} horizon"""
        return True

    def register(self, digit, loc, seed=None):
        """Registers digit

        Args:
            digit (Digit): digit instance to register
            loc (tuple[int]): upper-left corner if 2-tuple, upper-left and
                lower-right corners if 4-tuple
        """
        # Ensure digit dimensionality and horizon match product's
        self._assert_compatible(digit)

        # If digit has an idx, use it
        if digit.idx:
            idx = digit.idx
        # Else create a new one
        else:
            idx = len(self)
            digit.set_idx(idx)

        # Apply product defined random geometric augmentation
        digit = self._augment_digit(digit=digit, seed=seed)
        self[idx] = (loc, digit)
        digit.affiliate()

    @setseed('numpy')
    def random_register(self, digit, seed=None):
        """Registers digit to product applying random strategy
        for the choice of its patching location

        Args:
            digit (Digit): digit instance to register
            seed (int): random seed (default: None)
        """
        if self.mode == 'random':
            # Draw random patching location and register
            loc = self._rdm_loc(seed=seed)
        elif self.mode == 'grid':
            try:
                # Choose unfilled location from grid
                loc = next(self._shuffled_grid)

            except StopIteration:
                raise IndexError("Trying to register too many digits, no space left on grid")
        self.register(digit, loc, seed)

    @setseed('random')
    def _augment_digit(self, digit, seed=None):
        """If defined, applies transformation to digit
        Args:
            digit (Digit)
            seed (int): random seed (default: None)
        Returns:
            type: digit
        """
        # If product defines digits transformation, use it
        if self.digit_transform:
            augmented_digit = self.digit_transform(digit)
            augmented_digit = digit._new(augmented_digit.im)
        # Else use digit as is
        else:
            aug_digit = digit
        return aug_digit

    def view(self):
        """Generates grayscale image of background with patched digits
        Returns:
            type: PIL.Image.Image
        """
        # Copy background image
        img = self.bg.copy()
        for loc, digit in self.values():
            # Compute upper-left corner position
            upperleft_loc = self.center2upperleft(loc, digit.size)
            # Paste on background with transparency mask
            img.paste(digit, upperleft_loc, mask=digit)
        return img

    def prepare(self):
        """Prepares product for generation by unfreezing digits and setting up
        some hidden cache attributes
        iteration
        """
        for _, digit in self.values():
            digit.unfreeze()
        # Save array version of background in cache
        bg_array = np.expand_dims(self.bg, -1)
        bg_array = np.tile(bg_array, self.nbands).astype(np.float64)
        self.bg.array = bg_array

    def generate(self, output_dir, astype='h5'):
        """Runs generation as two for loops :
        ```
        for time_step in horizon:
            for digit in registered_digits:
                Scale digit with its next time serie slice
                Patch digit on background
            Save resulting image
        ```
        Args:
            output_dir (str): path to output directory
            astype (str): in {'h5', 'jpg'}
        """
        # Prepare product and export
        self.prepare()

        with ProductExport(output_dir, astype) as export:
            index = export._init_generation_index(self)
            bar = Bar("Generation", max=self.horizon)

            for i in range(self.horizon):
                # Create copies of background to preserve original
                img = self.bg.array.copy()
                annotation = np.zeros(self.bg.size + (2,))

                for idx, (loc, digit) in self.items():
                    # Update digit in size and pixel values
                    patch = next(digit)
                    # Make annotation mask
                    annotation_mask = digit.annotation_mask_from(patch_array=patch)
                    # Patch on background
                    self.patch_array(img, patch, loc)
                    self.patch_array(annotation, annotation_mask, loc)

                frame_name = '.'.join([f"frame_{i}", astype])
                annotation_name = f"annotation_{i}.h5"

                # Record in index
                index['files'][i] = frame_name
                index['features']['nframes'] += 1

                # Dump file
                export.dump_frame(img, frame_name)
                export.dump_annotation(annotation, annotation_name)
                bar.next()

            # Save index
            export.dump_index(index)

    @setseed('numpy')
    def _rdm_loc(self, seed=None):
        """Draws random location based on product background dimensions

        Args:
            seed (int): random seed
        """
        x = int(self.bg.width * self.rdm_dist())
        y = int(self.bg.height * self.rdm_dist())
        return x, y

    @staticmethod
    def patch_array(bg_array, patch_array, loc):
        """Inplace patching of numpy array into another numpy array
        Patch is cropped if needed to handle out-of-boundaries patching

        Args:
            bg_array (np.ndarray): background array, valued in [0, 1]
            patch_array (np.ndarray): array to patch, valued in [0, 1]
            loc (tuple[int]): patching location

        Returns:
            type: None
        """
        upperleft_loc = Product.center2upperleft(loc, patch_array.shape[:2])
        y, x = upperleft_loc
        # Crop patch if out-of-bounds upper-left patching location
        if x < 0:
            patch_array = patch_array[-x:]
            x = 0
        if y < 0:
            patch_array = patch_array[:, -y:]
            y = 0
        w, h = patch_array.shape[:2]

        # Again crop if out-of-bounds lower-right patching location
        w = min(w, bg_array.shape[0] - x)
        h = min(h, bg_array.shape[1] - y)

        # Patch and clip
        bg_array[x:x + w, y:y + h] += patch_array[:w, :h]
        bg_array.clip(max=1)

    @staticmethod
    def center2upperleft(loc, patch_size):
        y, x = loc
        w, h = patch_size
        upperleft_loc = (y - w // 2, x - h // 2)
        return upperleft_loc

    @property
    def size(self):
        return self._size

    @property
    def mode(self):
        return self._mode

    @property
    def bg(self):
        return self._bg

    @property
    def digit_transform(self):
        return self._digit_transform

    @property
    def rdm_dist(self):
        return self._rdm_dist

    @property
    def grid_size(self):
        return self._grid_size

    @property
    def grid(self):
        return self._grid

    @property
    def nbands(self):
        return self._nbands

    @property
    def horizon(self):
        return self._horizon

    @property
    def seed(self):
        return self._seed


class ProductExport:
    """Handler for product image dumping during generation or derivation step

    Args:
        output_dir (str): output directory
        astype (str): export type in {'h5', 'jpg'}
    """
    _frame_dirname = 'frames/'
    _annotation_dirname = 'annotations/'
    _frame_name = 'frame_{i:03d}'
    _annotation_name = 'annotation_{i:03d}.h5'
    _index_name = 'index.json'
    __frames_export_types__ = {'h5', 'jpg'}

    def __init__(self, output_dir, astype):
        if astype not in self.__frames_export_types__:
            raise TypeError("Unknown dumping type")
        self._output_dir = output_dir
        self._astype = astype

    def __enter__(self):
        self._setup_output_dir()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        return True

    def _setup_output_dir(self, output_dir=None, overwrite=False):
        """Builds output directory hierarchy structured as :

            directory_name/
            ├── frames/
            ├── annotations/
            └── index.json

        Args:
            output_dir (str): path to output directory
            overwrite (bool): if True and directory already exists, erases
                everything and recreates from scratch
        """
        output_dir = output_dir or self.output_dir
        frames_dir = os.path.join(output_dir, self._frame_dirname)
        annotations_dir = os.path.join(output_dir, self._annotation_dirname)
        mkdir(output_dir, overwrite=overwrite)
        mkdir(frames_dir)
        mkdir(annotations_dir)

    @staticmethod
    def _init_generation_index(product):
        """Initializes generation index

        Returns:
            type: dict
        """
        index = {'features': {'width': product.size[0],
                              'height': product.size[1],
                              'nbands': product.nbands,
                              'horizon': product.horizon,
                              'ndigit': len(product),
                              'nframes': 0},
                 'files': dict()}
        return index

    def dump_array(self, array, dump_path, name=""):
        """Dumps numpy array following hdf5 protocol

        Args:
            array (np.ndarray)
            dump_path (str)
            name (str): Optional dataset name
        """
        with h5py.File(dump_path, 'w') as f:
            f.create_dataset(name, data=array)

    def dump_jpg(self, array, dump_path):
        """Dumps numpy array as jpg image
        Only compatible with 3-bands product

        Args:
            array (np.ndarray)
            dump_path (str)
        """
        assert array.ndim == 3 and array.shape[-1] == 3, "RGB image generation only available for 3-bands products"
        img = Image.fromarray((array * 255).astype(np.uint8), mode='RGB')
        img.save(dump_path)

    def dump_frame(self, frame, filename, astype=None):
        """Dumps numpy array of imagery frame at specified location
        Handles jpg format for 3-bands products only

        Args:
            frame (np.ndarray): array to dump
            filename (str): dumped file name
            astype (str): in {'h5', 'jpg'}
        """
        astype = astype or self.astype
        dump_path = os.path.join(self.output_dir, self._frame_dirname, filename)
        if astype == 'h5':
            self.dump_array(array=frame, dump_path=dump_path, name=filename)
        elif astype == 'jpg':
            self.dump_jpg(array=frame, dump_path=dump_path)
        else:
            raise TypeError("Unknown dumping type")

    def dump_annotation(self, annotation, filename):
        """Dumps annotation mask under annotation directory

        Args:
            annotation (np.ndarray): array to dump
            filename (str): dumped file name

        Returns:
            type: Description of returned object.
        """
        dump_path = os.path.join(self.output_dir, self._annotation_dirname, filename)
        self.dump_array(array=annotation, dump_path=dump_path, name=filename)

    def dump_index(self, index):
        """Simply saves index as json file under export directory

        Args:
            index (dict)
        """
        index_path = os.path.join(self.output_dir, self._index_name)
        save_json(path=index_path, jsonFile=index)

    @property
    def output_dir(self):
        return self._output_dir

    @property
    def astype(self):
        return self._astype


class ProductDataset(Dataset):
    """Dataset loading class for generated products

    Very straigthforward implementation to be adapted to product dumping
        format

    Args:
        root (str): path to directory where product has been dumped
    """
    def __init__(self, root):
        self._root = root
        index_path = os.path.join(root, ProductExport._index_name)
        data_path = os.path.join(root, ProductExport._dump_dir_name)
        self._index = load_json(index_path)
        self._files_path = {int(key): os.path.join(data_path, filename) for (key, filename) in self.index['files'].items()}

    def __getitem__(self, idx):
        path = self._files_path[idx]
        return np.load(path)

    def __len__(self):
        return len(self._files_path)

    @property
    def root(self):
        return self._root

    @property
    def index(self):
        return self._index
