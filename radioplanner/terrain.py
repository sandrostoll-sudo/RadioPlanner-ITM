"""Indexed offline GeoTIFF elevation sampling, shared by all modules."""
from pathlib import Path
from collections import OrderedDict
import math

class TerrainError(Exception):
    pass

class TerrainSampler:
    """Index bounds once; reuse open rasters, and transform each point once per CRS."""
    def __init__(self, folder, max_open=12):
        try:
            import rasterio
            from rasterio.warp import transform_bounds
        except ImportError as exc:
            raise TerrainError('GeoTIFF-Unterstützung fehlt. Bitte installieren: py -3.12 -m pip install rasterio') from exc
        self.rasterio=rasterio
        root=Path(folder)
        if not root.is_dir():
            raise TerrainError('Geländedatenordner existiert nicht.')
        paths=sorted(p for p in root.rglob('*') if p.suffix.lower() in ('.tif','.tiff'))
        if not paths:
            raise TerrainError('Keine GeoTIFF-Dateien im gewählten Ordner gefunden.')
        self.entries=[];self.open_files=OrderedDict();self.max_open=max_open
        for path in paths:
            try:
                with rasterio.open(path) as ds:
                    if ds.crs is None or ds.count<1:continue
                    west,south,east,north=transform_bounds(ds.crs,'EPSG:4326',*ds.bounds,densify_pts=8)
                    resolution=max(abs(ds.res[0]),abs(ds.res[1])) if ds.crs.is_projected else float('inf')
                    self.entries.append((west,south,east,north,resolution,path))
            except (OSError,ValueError,RuntimeError):
                continue
        if not self.entries:
            raise TerrainError('Keine lesbaren GeoTIFFs mit Koordinatensystem gefunden. Bitte DGM-Dateien prüfen.')
        self.entries.sort(key=lambda x:x[4])
    def close(self):
        for ds in self.open_files.values():ds.close()
        self.open_files.clear()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def _dataset(self,path):
        if path in self.open_files:
            self.open_files.move_to_end(path)
            return self.open_files[path]
        ds=self.rasterio.open(path)
        self.open_files[path]=ds
        if len(self.open_files)>self.max_open:
            _,old=self.open_files.popitem(last=False);old.close()
        return ds
    def diagnose(self, latitude, longitude):
        """Explain missing heights without claiming that a tile can be downloaded."""
        from rasterio.warp import transform
        matching = []
        errors = []
        for west, south, east, north, resolution, path in self.entries:
            if not (west <= longitude <= east and south <= latitude <= north):
                continue
            try:
                ds = self._dataset(path)
                x, y = transform('EPSG:4326', ds.crs, [longitude], [latitude])
                if not (ds.bounds.left <= x[0] < ds.bounds.right and ds.bounds.bottom < y[0] <= ds.bounds.top):
                    continue
                row, col = ds.index(x[0], y[0])
                if not (0 <= row < ds.height and 0 <= col < ds.width):
                    continue
                pixel = ds.read(1, window=((row,row+1),(col,col+1)), masked=True)[0,0]
                valid = not bool(getattr(pixel, 'mask', False)) and math.isfinite(float(pixel))
                matching.append({'file':str(path), 'crs':str(ds.crs), 'valid':valid,
                                 'height':float(pixel) if valid else None})
            except (OSError,ValueError,RuntimeError) as exc:
                errors.append(f'{path.name}: {exc}')
        return {'latitude':latitude, 'longitude':longitude,
                'matching':matching, 'errors':errors,
                'status':('valid' if any(x['valid'] for x in matching) else
                          'nodata' if matching else 'uncovered')}

    def sample_fast(self, latitude, longitude):
        """Block-cached sampling for dense DMR profiles; same pixel/priority rules as sample()."""
        from rasterio.windows import Window
        from rasterio.warp import transform
        if not hasattr(self, '_fast_blocks'):
            self._fast_blocks = OrderedDict()
            self._fast_transforms = {}
            self._fast_stats = {'samples': 0, 'block_reads': 0, 'block_hits': 0}
        self._fast_stats['samples'] += 1
        for west, south, east, north, resolution, path in self.entries:
            if not (west <= longitude <= east and south <= latitude <= north):
                continue
            try:
                ds = self._dataset(path)
                # Reuse the CRS transformer; avoid creating one per sample.
                crs_key = str(ds.crs)
                if crs_key not in self._fast_transforms:
                    from pyproj import Transformer
                    self._fast_transforms[crs_key] = Transformer.from_crs('EPSG:4326', ds.crs, always_xy=True)
                x, y = self._fast_transforms[crs_key].transform(longitude, latitude)
                if not (ds.bounds.left <= x < ds.bounds.right and ds.bounds.bottom < y <= ds.bounds.top):
                    continue
                row, col = ds.index(x, y)
                if not (0 <= row < ds.height and 0 <= col < ds.width):
                    continue
                block_row, block_col = row // 64, col // 64
                key = (path, block_row, block_col)
                if key in self._fast_blocks:
                    self._fast_blocks.move_to_end(key)
                    block = self._fast_blocks[key]
                    self._fast_stats['block_hits'] += 1
                else:
                    top, left = block_row * 64, block_col * 64
                    block = ds.read(1, window=Window(left, top, min(64, ds.width-left), min(64, ds.height-top)), masked=True)
                    self._fast_blocks[key] = block
                    self._fast_stats['block_reads'] += 1
                    if len(self._fast_blocks) > 96:
                        self._fast_blocks.popitem(last=False)
                pixel = block[row-block_row*64, col-block_col*64]
                if bool(getattr(pixel, 'mask', False)):
                    continue
                height = float(pixel)
                if not math.isfinite(height):
                    continue
                return dict(height=height, source=str(path), resolution=None if math.isinf(resolution) else resolution,
                            crs=str(ds.crs), vertical_datum='Unbekannt (Datensatz-Metadaten prüfen)')
            except (OSError, ValueError, RuntimeError):
                continue
        raise TerrainError(f'Kein gültiger DGM-Höhenwert bei {latitude:.6f} N, {longitude:.6f} E. Abdeckung und GeoTIFF-Inhalt prüfen.')

    def sample(self,latitude,longitude):
        from rasterio.warp import transform
        for west,south,east,north,resolution,path in self.entries:
            if not (west<=longitude<=east and south<=latitude<=north):continue
            try:
                ds=self._dataset(path)
                x,y=transform('EPSG:4326',ds.crs,[longitude],[latitude]);x=x[0];y=y[0]
                if not (ds.bounds.left<=x<ds.bounds.right and ds.bounds.bottom<y<=ds.bounds.top):continue
                row,col=ds.index(x,y)
                if not (0<=row<ds.height and 0<=col<ds.width):continue
                pixel=ds.read(1,window=((row,row+1),(col,col+1)),masked=True)[0,0]
                if bool(getattr(pixel,'mask',False)):continue
                height=float(pixel)
                if not math.isfinite(height):continue
                return dict(height=height,source=str(path),resolution=None if math.isinf(resolution) else resolution,
                            crs=str(ds.crs),vertical_datum='Unbekannt (Datensatz-Metadaten prüfen)')
            except (OSError,ValueError,RuntimeError):
                continue
        raise TerrainError(f'Kein gültiger DGM-Höhenwert bei {latitude:.6f} N, {longitude:.6f} E. Abdeckung und GeoTIFF-Inhalt prüfen.')

def sample_folder(folder,latitude,longitude):
    with TerrainSampler(folder) as sampler:
        return sampler.sample(latitude,longitude)
