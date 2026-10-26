import os
import io
import time
import requests
import zipfile
import itertools
from math import sqrt
import array
import numpy

try:
    from osgeo import gdal
    has_gdal = True
    gdal.UseExceptions()
except:
    has_gdal = False
from PIL import Image
import O4_UI_Utils as UI
import O4_File_Names as FNAMES
import O4_Version

available_sources = (
    "View",
    "Viewfinderpanoramas (J. de Ferranti) - mostly worldwide",
    "SRTM",
    "SRTMv3 (from OpenTopography) - worldwide at latitudes < 60",
    "NED1",
    'NED 1" (from USGS) - USA, Canada, Mexico',
    "NED1/3",
    'NED 1/3" (from USGS) - USA',
    "ALOS",
    "ALOS 3W30 average (from OpenTopography) - mostly worldwide",
)

# Tile configs of version 1.40 name the sources with these labels.
renamed_sources = {
    "SRTMv3 (from OpenTopography) - NOW REQUIRES MANUAL DOWNLOAD": (
        available_sources[3]
    ),
    "ALOS 3W30 (from OpenTopography) - NOW REQUIRES MANUAL DOWNLOAD": (
        available_sources[9]
    ),
}

global_sources = ("View", "SRTM", "ALOS")

# The public bucket of OpenTopography, https://opentopography.org/
opentopography_url = "https://opentopography.s3.sdsc.edu/raster/"

################################################################################
class DEM:
    def __init__(self, lat, lon, source="", fill_nodata=True, info_only=False):
        self.lat = lat
        self.lon = lon
        source = source.replace("{latlon}", FNAMES.hem_latlon(lat, lon))
        if ";" in source:
            self.alt = self.alt_composite
            self.alt_vec = self.alt_vec_composite
        else:
            self.alt = self.alt_nostrict
            self.alt_vec = self.alt_vec_nostrict
        self.load_data(source, info_only)
        if info_only:
            return
        if fill_nodata == "to zero":
            self.nodata_to_zero()
        elif fill_nodata:
            if not fill_nodata_values_with_nearest_neighbor(
                self.alt_dem, self.nodata
            ):
                UI.vprint(
                    1,
                    "   INFO: Dataset contains too much no_data to be filled.",
                )
                self.nodata_to_zero()

        UI.vprint(
            1,
            "    * Min altitude:",
            self.alt_dem.min(),
            ", Max altitude:",
            self.alt_dem.max(),
            ", Mean:",
            self.alt_dem.mean(),
        )

    def load_data(self, source, info_only=False):
        if not source:
            if os.path.exists(FNAMES.generic_tif(self.lat, self.lon)):
                source = FNAMES.generic_tif(self.lat, self.lon)
            else:
                source = available_sources[1]
        if ";" in source:
            source, local_sources = source.split(";")[0], source.split(";")[1:]
        else:
            local_sources = None
        source = renamed_sources.get(source, source)
        if source in available_sources[1::2]:
            short_source = available_sources[
                available_sources.index(source) - 1
            ]
            if short_source in global_sources:
                (
                    self.epsg,
                    self.x0,
                    self.y0,
                    self.x1,
                    self.y1,
                    self.nodata,
                    self.nxdem,
                    self.nydem,
                    self.alt_dem,
                ) = build_combined_raster(
                    short_source, self.lat, self.lon, info_only
                )
            else:
                if ensure_elevation(short_source, self.lat, self.lon):
                    (
                        self.epsg,
                        self.x0,
                        self.y0,
                        self.x1,
                        self.y1,
                        self.nodata,
                        self.nxdem,
                        self.nydem,
                        self.alt_dem,
                    ) = read_elevation_from_file(
                        FNAMES.elevation_data(short_source, self.lat, self.lon),
                        self.lat,
                        self.lon,
                        info_only,
                        3601,
                    )
                else:
                    (
                        self.epsg,
                        self.x0,
                        self.y0,
                        self.x1,
                        self.y1,
                        self.nodata,
                        self.nxdem,
                        self.nydem,
                        self.alt_dem,
                    ) = (
                        4326,
                        0,
                        0,
                        1,
                        1,
                        -32768,
                        3601,
                        3601,
                        numpy.zeros((3601, 3601), dtype=numpy.float32),
                    )
        else:
            file_name = source
            (
                self.epsg,
                self.x0,
                self.y0,
                self.x1,
                self.y1,
                self.nodata,
                self.nxdem,
                self.nydem,
                self.alt_dem,
            ) = read_elevation_from_file(
                file_name, self.lat, self.lon, info_only
            )
        if not local_sources:
            return
        self.subdems = tuple()
        for local_source in local_sources:
            self.subdems += (
                DEM(self.lat, self.lon, local_source, False, info_only),
            )
            self.subdems[-1].alt = self.subdems[-1].alt_strict
            self.subdems[-1].alt_vec = self.subdems[-1].alt_vec_strict

    def nodata_to_zero(self):
        if (self.alt_dem == self.nodata).any():
            UI.vprint(1, "   INFO: Replacing nodata nodes with zero altitude.")
            self.alt_dem[self.alt_dem == self.nodata] = 0
        self.nodata = -32768
        return

    def write_to_file(self, filename):
        self.alt_dem.astype(numpy.float32).tofile(filename)
        return

    def super_level_set(self, level, wgs84_bbox):
        (lonmin, lonmax, latmin, latmax) = wgs84_bbox
        xmin = lonmin - self.lon
        xmax = lonmax - self.lon
        ymin = latmin - self.lat
        ymax = latmax - self.lat
        if xmin < self.x0:
            xmin = self.x0
        if xmax > self.x1:
            xmax = self.x1
        if ymin < self.y0:
            ymin = self.y0
        if ymax > self.y1:
            ymax = self.y1
        pixx0 = round((xmin - self.x0) / (self.x1 - self.x0) * (self.nxdem - 1))
        pixx1 = round((xmax - self.x0) / (self.x1 - self.x0) * (self.nxdem - 1))
        pixy0 = round((self.y1 - ymax) / (self.y1 - self.y0) * (self.nydem - 1))
        pixy1 = round((self.y1 - ymin) / (self.y1 - self.y0) * (self.nydem - 1))
        return (
            (
                xmin + self.lon,
                xmax + self.lon,
                ymin + self.lat,
                ymax + self.lat,
            ),
            self.alt_dem[pixy0 : pixy1 + 1, pixx0 : pixx1 + 1] >= level,
        )

    def alt_nostrict(self, node):
        Nx = self.nxdem - 1
        Ny = self.nydem - 1
        x = node[0]
        y = node[1]
        x = max(x, self.x0)
        x = min(x, self.x1)
        y = max(y, self.y0)
        y = min(y, self.y1)
        px = (x - self.x0) / (self.x1 - self.x0) * Nx
        py = (y - self.y0) / (self.y1 - self.y0) * Ny
        nx = int(px)
        Nminusny = Ny - int(py)
        rx = px - nx
        ry = py + Nminusny - Ny
        t1 = self.alt_dem[Nminusny, nx]
        t2 = self.alt_dem[
            (Nminusny - 1) * (Nminusny >= 1),
            (nx + 1) * (nx < Nx) + Nx * (nx == Nx),
        ]
        t3 = self.alt_dem[Nminusny, (nx + 1) * (nx < Nx) + Nx * (nx == Nx)]
        t4 = self.alt_dem[(Nminusny - 1) * (Nminusny >= 1), nx]
        return ((1 - rx) * t1 + ry * t2 + (rx - ry) * t3) * (rx >= ry) + (
            (1 - ry) * t1 + rx * t2 + (ry - rx) * t4
        ) * (rx < ry)

    def alt_strict(self, node):
        x = node[0]
        y = node[1]
        return (
            self.nodata
            if (
                (x > self.x1) or (x < self.x0) or (y < self.y0) or (y > self.y1)
            )
            else self.alt_dem[
                int(
                    round(
                        (self.y1 - y) / (self.y1 - self.y0) * (self.nydem - 1)
                    )
                ),
                int(
                    round(
                        (x - self.x0) / (self.x1 - self.x0) * (self.nxdem - 1)
                    )
                ),
            ]
        )

    def alt_composite(self, node):
        for subdem in self.subdems[::-1]:
            tmp = subdem.alt_strict(node)
            if tmp != subdem.nodata:
                return tmp
        return self.alt_nostrict(node)

    def alt_vec_nostrict(self, way):
        Nx = self.nxdem - 1
        Ny = self.nydem - 1
        x, y = way[:, 0], way[:, 1]
        x = numpy.maximum.reduce([x, self.x0 * numpy.ones(x.shape)])
        x = numpy.minimum.reduce([x, self.x1 * numpy.ones(x.shape)])
        y = numpy.maximum.reduce([y, self.y0 * numpy.ones(y.shape)])
        y = numpy.minimum.reduce([y, self.y1 * numpy.ones(y.shape)])
        px = (x - self.x0) / (self.x1 - self.x0) * Nx
        py = (y - self.y0) / (self.y1 - self.y0) * Ny
        nx = px.astype(numpy.int64)
        Nminusny = Ny - py.astype(numpy.int64)
        rx = px - nx
        ry = py + Nminusny - Ny
        t1 = [self.alt_dem[i][j] for i, j in zip(Nminusny, nx)]
        t2 = [
            self.alt_dem[i][j]
            for i, j in zip(
                (Nminusny - 1) * (Nminusny >= 1),
                (nx + 1) * (nx < Nx) + Nx * (nx == Nx),
            )
        ]
        t3 = [
            self.alt_dem[i][j]
            for i, j in zip(Nminusny, (nx + 1) * (nx < Nx) + Nx * (nx == Nx))
        ]
        t4 = [
            self.alt_dem[i][j]
            for i, j in zip((Nminusny - 1) * (Nminusny >= 1), nx)
        ]
        return ((1 - rx) * t1 + ry * t2 + (rx - ry) * t3) * (rx >= ry) + (
            (1 - ry) * t1 + rx * t2 + (ry - rx) * t4
        ) * (rx < ry)

    def alt_vec_strict(self, way):
        x, y = way[:, 0], way[:, 1]
        mask = (x >= self.x0) * (x <= self.x1) * (y >= self.y0) * (y <= self.y1)
        nx = numpy.round(
            (x - self.x0) / (self.x1 - self.x0) * (self.nxdem - 1)
        ).astype(numpy.int64)
        Nminusny = numpy.round(
            (self.y1 - y) / (self.y1 - self.y0) * (self.nydem - 1)
        ).astype(numpy.int64)
        return numpy.array(
            [
                self.alt_dem[i][j] if k else self.nodata
                for i, j, k in zip(Nminusny, nx, mask)
            ]
        )

    def alt_vec_composite(self, way):
        tmp = self.alt_vec_nostrict(way)
        for subdem in self.subdems:
            tmp2 = subdem.alt_vec_strict(way)
            tmp[tmp2 != subdem.nodata] = tmp2[tmp2 != subdem.nodata]
        return tmp

################################################################################
def build_combined_raster(source, lat, lon, info_only):
    world_tiles = numpy.array(
        Image.open(os.path.join(FNAMES.Utils_dir, "world_tiles.png"))
    )
    if source in ("View", "SRTM"):
        base = 3601
        overlap = 1
        beyond = 36
        x0 = y0 = -0.01
        x1 = y1 = 1.01
        epsg = 4326
        nodata = -32768
        nxdem = nydem = base + 2 * beyond  # = 3673
    elif source == ("ALOS"):
        base = 3600
        overlap = 0
        beyond = 36
        eps = 1 / 7200
        x0 = y0 = -0.01 + eps
        x1 = y1 = 1.01 - eps
        epsg = 4326
        nodata = -32768
        nxdem = nydem = base + 2 * beyond  # = 3672
    if info_only:
        return (epsg, x0, y0, x1, y1, nodata, nxdem, nydem, None)
    alt_dem = numpy.zeros((nydem, nxdem), dtype=numpy.float32)
    for (lat0, lon0) in itertools.product(
        (lat, lat - 1, lat + 1), (lon, lon - 1, lon + 1)
    ):
        verbose = True if (lat0 == lat and lon0 == lon) else False
        x = (180 + lon0) % 360
        y = 89 - lat0
        if not world_tiles[y, x]:
            tmparray = numpy.zeros((base, base), dtype=numpy.float32)
        elif ensure_elevation(source, lat0, (lon0 + 180) % 360 - 180, verbose):
            tmparray = read_elevation_from_file(
                FNAMES.elevation_data(source, lat0, (lon0 + 180) % 360 - 180),
                lat0,
                (lon0 + 180) % 360 - 180,
                info_only,
                base,
            )[-1]
        else:
            tmparray = numpy.zeros((base, base), dtype=numpy.float32)
        by = beyond
        ov = overlap
        if lat0 == lat and lon0 == lon:
            alt_dem[by:-by, by:-by] = tmparray
        elif lat0 == lat and lon0 == lon - 1:
            alt_dem[by:-by, :by] = (
                tmparray[:, -by - ov : -ov] if ov else tmparray[:, -by:]
            )
        elif lat0 == lat and lon0 == lon + 1:
            alt_dem[by:-by, -by:] = (
                tmparray[:, ov : ov + by] if ov else tmparray[:, :by]
            )
        elif lat0 == lat + 1 and lon0 == lon:
            alt_dem[:by, by:-by] = (
                tmparray[-ov - by : -ov, :] if ov else tmparray[-by:, :]
            )
        elif lat0 == lat - 1 and lon0 == lon:
            alt_dem[-by:, by:-by] = (
                tmparray[ov : ov + by, :] if ov else tmparray[:by, :]
            )
        elif lat0 == lat + 1 and lon0 == lon - 1:
            alt_dem[:by, :by] = (
                tmparray[-ov - by : -ov, -ov - by : -ov]
                if ov
                else tmparray[-by:, -by:]
            )
        elif lat0 == lat + 1 and lon0 == lon + 1:
            alt_dem[:by, -by:] = (
                tmparray[-ov - by : -ov, ov : ov + by]
                if ov
                else tmparray[-by:, :by]
            )
        elif lat0 == lat - 1 and lon0 == lon - 1:
            alt_dem[-by:, :by] = (
                tmparray[ov : ov + by, -ov - by : -ov]
                if ov
                else tmparray[:by, -by:]
            )
        elif lat0 == lat - 1 and lon0 == lon + 1:
            alt_dem[-by:, -by:] = (
                tmparray[ov : ov + by, ov : ov + by]
                if ov
                else tmparray[:by, :by]
            )
    return (epsg, x0, y0, x1, y1, nodata, nxdem, nydem, alt_dem)

################################################################################
def read_elevation_from_file(
    file_name, lat, lon, info_only=False, base_if_error=3601
):
    alt_dem = None
    if file_name[-4:].lower() == ".hgt":
        x0 = y0 = 0
        x1 = y1 = 1
        epsg = 4326
        nodata = -32768
        try:
            nxdem = nydem = int(round(sqrt(os.path.getsize(file_name) / 2)))
            if not info_only:
                alt_dem = (
                    numpy.fromfile(file_name, numpy.dtype(">i2"))
                    .astype(numpy.float32)
                    .reshape((nydem, nxdem))
                )
            if nxdem == 1201:
                nxdem = nydem = 3601
                if not info_only:
                    fill_nodata_values_with_nearest_neighbor(alt_dem, nodata)
                    alt_dem = upsample(alt_dem)
        except Exception as e:
            print(e)
            UI.lvprint(
                1,
                "    ERROR: in reading elevation from",
                file_name,
                "-> replaced with zero altitude.",
            )
            nxdem = nydem = base_if_error
            if not info_only:
                alt_dem = numpy.zeros(
                    (base_if_error, base_if_error), dtype=numpy.float32
                )

    elif file_name[-4:].lower() == ".raw":
        try:
            nxdem = nydem = int(round(sqrt(os.path.getsize(file_name) / 2)))
            f = open(file_name, "rb")
            alt = array.array("h")
            alt.fromfile(f, nxdem * nydem)
            f.close()
            if not info_only:
                alt_dem = numpy.asarray(alt, dtype=numpy.float32).reshape(
                    (nxdem, nydem)
                )[::-1]
        except:
            UI.lvprint(
                1,
                "    ERROR: in reading elevation from",
                file_name,
                "-> replaced with zero altitude.",
            )
            nxdem = nydem = base_if_error
            if not info_only:
                alt_dem = numpy.zeros(
                    (base_if_error, base_if_error), dtype=numpy.float32
                )
        x0 = y0 = 0
        x1 = y1 = 1
        epsg = 4326
        nodata = -32768
    elif has_gdal or file_name.lower().endswith((".tif", ".tiff")):
        try:
            read_raster = read_raster_gdal if has_gdal else read_geotiff
            (alt_dem, geo, nodata, epsg, nxdem, nydem) = read_raster(
                file_name, info_only
            )
            if nodata is None:
                UI.vprint(
                    1,
                    "    WARNING: raster DEM does not advertise its no_data ",
                    "value, assuming -32768.",
                )
                nodata = -32768
            else:  
                # elevations being stored as float32, we push the nodata to that 
                # framework too, and then replace no_data values by -32768 
                # anyway for uniformity
                nodata = numpy.float32(nodata)
                if not info_only:
                    alt_dem[alt_dem == nodata] = -32768
                nodata = -32768
            if epsg is None:
                UI.vprint(
                    1,
                    "    WARNING: raster DEM does not advertise its EPSG ",
                    "code, assuming 4326.",
                )
                epsg = 4326
            if epsg not in (
                4326,
                4269,
            ):  
            # let's be blind about 4269 which might be sufficiently close to 
            # 4326 for our purposes
                UI.lvprint(
                    1,
                    "    WARNING: unsupported EPSG code ",
                    epsg,
                    ". Only EPSG:4326 is supported, result is likely to ",
                    "be non sense.",
                )
            # We are assuming AREA_OR_POINT is area here
            x0 = geo[0] + 0.5 * geo[1] - lon
            y1 = geo[3] + 0.5 * geo[5] - lat
            x1 = x0 + (nxdem - 1) * geo[1]
            y0 = y1 + (nydem - 1) * geo[5]
        except:
            UI.lvprint(
                1,
                "   ERROR: in reading ",
                file_name,
                "-> replaced with zero altitude.",
            )
            nxdem = nydem = base_if_error
            if not info_only:
                alt_dem = numpy.zeros(
                    (base_if_error, base_if_error), dtype=numpy.float32
                )
            x0 = y0 = 0
            x1 = y1 = 1
            epsg = 4326
            nodata = -32768
    else:
        UI.lvprint(
            1,
            "   WARNING: unsupported raster (install Gdal):",
            file_name,
            "-> replaced with zero altitude.",
        )
        nxdem = nydem = base_if_error
        if not info_only:
            alt_dem = numpy.zeros(
                (base_if_error, base_if_error), dtype=numpy.float32
            )
        x0 = y0 = 0
        x1 = y1 = 1
        epsg = 4326
        nodata = -32768
    return (epsg, x0, y0, x1, y1, nodata, nxdem, nydem, alt_dem)


def read_raster_gdal(file_name, info_only):
    ds = gdal.Open(file_name)
    rs = ds.GetRasterBand(1)
    alt_dem = None if info_only else rs.ReadAsArray().astype(numpy.float32)
    try:
        epsg = int(ds.GetProjection().split('"')[-2])
    except (IndexError, ValueError):
        epsg = None
    return (
        alt_dem,
        ds.GetGeoTransform(),
        rs.GetNoDataValue(),
        epsg,
        ds.RasterXSize,
        ds.RasterYSize,
    )


# Reads a GeoTIFF with Pillow, and georeferences it as GDAL does from the
# tags ModelPixelScale (33550), ModelTiepoint (33922), and GeoKeyDirectory
# (34735) of OGC GeoTIFF 1.1 (https://docs.ogc.org/is/19-008r4/19-008r4.html),
# and the nodata tag of GDAL (42113).
def read_geotiff(file_name, info_only):
    with Image.open(file_name) as im:
        tags = im.tag_v2
        (sx, sy) = tags[33550][:2]
        (i, j, _, x, y, _) = tags[33922][:6]
        # Four shorts per key after a header of four: the key, the tag that
        # holds its value (0 when the entry holds it), a count, and the value.
        keys = tags.get(34735, ())
        geokeys = {
            keys[n]: keys[n + 3]
            for n in range(4, len(keys) - 3, 4)
            if keys[n + 1] == 0
        }
        nodata = float(tags[42113]) if 42113 in tags else None
        alt_dem = None if info_only else numpy.array(im, dtype=numpy.float32)
        (nxdem, nydem) = im.size
    # The EPSG code is the ProjectedCSType key, or else the GeographicType key.
    epsg = geokeys.get(3072, geokeys.get(2048))
    # GTRasterType 2 is PixelIsPoint: the tiepoint is the center of a pixel,
    # and GDAL moves it to the pixel's corner.
    if geokeys.get(1025) == 2:
        (x, y) = (x - sx / 2, y + sy / 2)
    geo = (x - i * sx, sx, 0.0, y + j * sy, 0.0, -sy)
    return (alt_dem, geo, nodata, epsg, nxdem, nydem)


##############################################################################

##############################################################################
def ensure_elevation(source, lat, lon, verbose=True):
    if source == "View":
        # Viewfinderpanorama grouping of files and resolutions is a 
        # bit complicated...
        if (lat, lon) in (
            (44, 5),
            (45, 5),
            (46, 5),
            (43, 6),
            (44, 6),
            (45, 6),
            (46, 6),
            (47, 6),
            (43, 7),
            (44, 7),
            (45, 7),
            (46, 7),
            (47, 7),
            (45, 8),
            (46, 8),
            (47, 8),
            (45, 9),
            (46, 9),
            (47, 9),
            (45, 10),
            (46, 10),
            (47, 10),
            (45, 11),
            (46, 11),
            (47, 11),
            (45, 12),
            (46, 12),
            (47, 12),
            (46, 13),
            (47, 13),
            (46, 14),
            (47, 14),
            (46, 15),
            (47, 15),
        ):
            resol = 1
            url = (
                "https://viewfinderpanoramas.org/dem1/"
                + os.path.basename(FNAMES.base_file_name(lat, lon))
                + ".zip"
            )
        else:
            deferranti_nbr = 31 + lon // 6
            if deferranti_nbr < 10:
                deferranti_nbr = "0" + str(deferranti_nbr)
            else:
                deferranti_nbr = str(deferranti_nbr)
            alphabet = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            deferranti_letter = (
                alphabet[lat // 4] if lat >= 0 else alphabet[(-1 - lat) // 4]
            )
            if lat < 0:
                deferranti_letter = "S" + deferranti_letter
            if deferranti_letter + deferranti_nbr in (
                "O31",
                "P31",
                "N32",
                "O32",
                "P32",
                "Q32",
                "N33",
                "O33",
                "P33",
                "Q33",
                "R33",
                "O34",
                "P34",
                "Q34",
                "R34",
                "O35",
                "P35",
                "Q35",
                "R35",
                "P36",
                "Q36",
                "R36",
            ):
                resol = 1
            else:
                resol = 3
            url = (
                "https://viewfinderpanoramas.org/dem"
                + str(resol)
                + "/"
                + deferranti_letter
                + deferranti_nbr
                + ".zip"
            )
        if os.path.exists(FNAMES.viewfinderpanorama(lat, lon)) and (
            resol == 3
            or os.path.getsize(FNAMES.viewfinderpanorama(lat, lon)) >= 25934402
        ):
            UI.vprint(2, "   Recycling ", FNAMES.viewfinderpanorama(lat, lon))
            return 1
        UI.vprint(
            1,
            "    Downloading ",
            FNAMES.viewfinderpanorama(lat, lon),
            "from Viewfinderpanoramas (J. de Ferranti).",
        )
        r = http_request(url, source, verbose)
        if not r:
            return 0
        try:
            zip_ref = zipfile.ZipFile(io.BytesIO(r.content), "r")
        except zipfile.BadZipFile:
            UI.lvprint(1, "    ERROR: The answer of", url, "is not a zip file.")
            return 0
        with zip_ref:
            for f in zip_ref.filelist:
                fname = os.path.basename(f.filename)
                if not fname:
                    continue
                try:
                    lat0 = int(fname[1:3])
                    lon0 = int(fname[4:7])
                except:
                    UI.vprint(
                        2,
                        "      Archive contains the unknown file name",
                        fname,
                        "which is skipped.",
                    )
                    continue
                if ("S" in fname) or ("s" in fname):
                    lat0 *= -1
                if ("W" in fname) or ("w" in fname):
                    lon0 *= -1
                out_filename = FNAMES.viewfinderpanorama(lat0, lon0)
                # we don't wish to overwrite a 1" version by downloading 
                # the whole archive of a nearby 3" one
                if (
                    not os.path.exists(out_filename)
                    or os.path.getsize(out_filename) <= f.file_size
                ):
                    if not os.path.isdir(os.path.dirname(out_filename)):
                        os.makedirs(os.path.dirname(out_filename))
                    with open(out_filename + ".part", "wb") as out:
                        UI.vprint(2, "      Extracting", out_filename)
                        out.write(zip_ref.open(f, "r").read())
                    os.replace(out_filename + ".part", out_filename)
    elif source in ("SRTM", "ALOS"):
        file_name = FNAMES.elevation_data(source, lat, lon)
        if os.path.exists(file_name):
            UI.vprint(2, "   Recycling ", file_name)
            return 1
        UI.vprint(1, "    Downloading ", file_name, "from OpenTopography.")
        if source == "SRTM":
            path = "SRTM_GL1/SRTM_GL1_srtm/" + FNAMES.hem_latlon(lat, lon)
            path += ".tif"
        else:
            path = "AW3D30/AW3D30_global/ALPSMLC30_{}{:03d}{}{:03d}_DSM.tif"
            path = path.format(
                "N" if lat >= 0 else "S",
                abs(lat),
                "E" if lon >= 0 else "W",
                abs(lon),
            )
        # The bucket answers 404 for a tile of sea, or outside the dataset.
        url = opentopography_url + path
        if not download_geotiff(url, file_name, source, verbose):
            if verbose:
                UI.lvprint(
                    1,
                    "    WARNING: No",
                    source,
                    "tile from OpenTopography for",
                    FNAMES.hem_latlon(lat, lon),
                    "-> zero altitude.",
                )
            return 0
    elif source in ("NED1", "NED1/3"):
        if os.path.exists(FNAMES.elevation_data(source, lat, lon)):
            UI.vprint(
                2, "   Recycling ", FNAMES.elevation_data(source, lat, lon)
            )
            return 1
        UI.vprint(
            1,
            "    Downloading ",
            FNAMES.elevation_data(source, lat, lon),
            "from USGS.",
        )
        nbr = "1" if source == "NED1" else "13"
        url_base = (
            "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/"
            + nbr + "/TIFF/current/"
        )
        tid = "n" if lat >= 0 else "s"
        tid += str(abs(lat + 1)).zfill(2)
        tid += "w" if lon < 0 else "e"
        tid += str(abs(lon)).zfill(3)
        url_base = url_base + tid + "/"
        usgs_name = (
            "USGS_" + nbr + "_" + tid + ".tif"
        )
        url = url_base + usgs_name
        file_name = FNAMES.elevation_data(source, lat, lon)
        if not download_geotiff(url, file_name, source, verbose):
            return 0
    else:
        UI.vprint(1, "   ERROR: Unknown elevation source.")
        return 0
    return 1

################################################################################
# Saves the GeoTIFF at url as file_name, through a temporary name, so that
# an interrupted download leaves nothing that Ortho4XP would read again.
# Returns 1 once the file is in place.
def download_geotiff(url, file_name, source, verbose):
    r = http_request(url, source, verbose)
    if not r:
        return 0
    if r.content[:4] not in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"):
        UI.lvprint(1, "    ERROR: The answer of", url, "is not a TIFF file.")
        return 0
    os.makedirs(os.path.dirname(file_name), exist_ok=True)
    with open(file_name + ".part", "wb") as f:
        f.write(r.content)
    os.replace(file_name + ".part", file_name)
    return 1


################################################################################
def http_request(url, source, verbose=False):
    s = requests.Session()
    tentative = 0
    while True:
        try:
            r = s.get(
                url, timeout=10, headers={"User-Agent": O4_Version.user_agent}
            )
            status_code = str(r)
            if "[20" in status_code:
                return r
            elif "[40" in status_code or "[30" in status_code:
                if verbose:
                    UI.vprint(2, "    Server said 'Not Found'")
                return 0
            elif "[5" in status_code:
                if verbose:
                    UI.vprint(
                        2, "    Server said 'Internal Error'.", status_code
                    )
            else:
                if verbose:
                    UI.vprint(2, status_code)
        except Exception as e:
            if verbose:
                UI.vprint(2, e)
        tentative += 1
        if tentative == 6:
            return 0
        UI.vprint(
            1,
            "    ",
            source,
            "server may be down or busy, new tentative in",
            2 ** tentative,
            "sec...",
        )
        time.sleep(2 ** tentative)

################################################################################
def fill_nodata_values_with_nearest_neighbor(alt_dem, nodata):
    step = 0
    while (alt_dem == nodata).any():
        if not step:
            if numpy.sum(alt_dem == nodata) >= 10000:
                return 0
            UI.vprint(
                2,
                "    INFO: Elevation file contains voids, trying to fill ",
                "them recursively by nearest neighbour.",
            )
        else:
            UI.vprint(2, "    ", step)
        alt10 = numpy.roll(alt_dem, 1, axis=0)
        alt10[0] = alt_dem[0]
        alt20 = numpy.roll(alt_dem, -1, axis=0)
        alt20[-1] = alt_dem[-1]
        alt01 = numpy.roll(alt_dem, 1, axis=1)
        alt01[:, 0] = alt_dem[:, 0]
        alt02 = numpy.roll(alt_dem, -1, axis=1)
        alt02[:, -1] = alt_dem[:, -1]
        if (nodata < 0):
            atemp = numpy.maximum(alt10, alt20)
            atemp = numpy.maximum(atemp, alt01)
            atemp = numpy.maximum(atemp, alt02)
        else:
            atemp = numpy.minimum(alt10, alt20)
            atemp = numpy.minimum(atemp, alt01)
            atemp = numpy.minimum(atemp, alt02)
        alt_dem[alt_dem == nodata] = atemp[alt_dem == nodata]
        step += 1
        if step > 20:
            UI.vprint(
                1,
                "    WARNING: The raster contain holes that seem to big to ",
                "be filled... I'm filling the remainder with zero.",
            )
            alt_dem[alt_dem == nodata] = 0
            break
    if step:
        UI.vprint(2, "    Done.")
    return 1

################################################################################
def upsample(alt_dem):
    # only implemented from 1201 to 3601, might be worth upgrading it some day
    alt_dem_tmp = numpy.zeros((3601, 3601), dtype=numpy.float32)
    for i in range(1201):
        alt_dem_tmp[3 * i, ::3] = alt_dem[i]
        alt_dem_tmp[3 * i, 1::3] = (
            2 / 3 * alt_dem[i, :-1] + 1 / 3 * alt_dem[i, 1:]
        )
        alt_dem_tmp[3 * i, 2::3] = (
            1 / 3 * alt_dem[i, :-1] + 2 / 3 * alt_dem[i, 1:]
        )
        if i == 1200:
            break
        alt_dem_tmp[3 * i + 1, ::3] = (
            2 / 3 * alt_dem[i] + 1 / 3 * alt_dem[i + 1]
        )
        alt_dem_tmp[3 * i + 2, ::3] = (
            1 / 3 * alt_dem[i] + 2 / 3 * alt_dem[i + 1]
        )
        alt_dem_tmp[3 * i + 1, 1::3] = (
            4 / 9 * alt_dem[i][:-1]
            + 2 / 9 * alt_dem[i, 1:]
            + 2 / 9 * alt_dem[i + 1, :-1]
            + 1 / 9 * alt_dem[i + 1, 1:]
        )
        alt_dem_tmp[3 * i + 2, 1::3] = (
            2 / 9 * alt_dem[i][:-1]
            + 1 / 9 * alt_dem[i, 1:]
            + 4 / 9 * alt_dem[i + 1, :-1]
            + 2 / 9 * alt_dem[i + 1, 1:]
        )
        alt_dem_tmp[3 * i + 1, 2::3] = (
            2 / 9 * alt_dem[i][:-1]
            + 4 / 9 * alt_dem[i, 1:]
            + 1 / 9 * alt_dem[i + 1, :-1]
            + 2 / 9 * alt_dem[i + 1, 1:]
        )
        alt_dem_tmp[3 * i + 2, 2::3] = (
            1 / 9 * alt_dem[i][:-1]
            + 2 / 9 * alt_dem[i, 1:]
            + 2 / 9 * alt_dem[i + 1, :-1]
            + 4 / 9 * alt_dem[i + 1, 1:]
        )
    return alt_dem_tmp

################################################################################
def smoothen(raster, pix_width, mask_im, preserve_boundary=True):
    if not pix_width:
        return raster
    if not mask_im:
        return raster
    tmp = numpy.array(raster)
    mask_array = numpy.array(mask_im, dtype=numpy.float32) / 255
    kernel = numpy.array(range(1, 2 * (pix_width + 1)))
    kernel[pix_width + 1 :] = range(pix_width, 0, -1)
    kernel = kernel / (pix_width + 1) ** 2
    tmp = tmp * mask_array
    tmpw = numpy.array(mask_array)
    for i in range(0, len(tmp)):
        tmp[i] = numpy.convolve(tmp[i], kernel)[pix_width:-pix_width]
        tmpw[i] = numpy.convolve(tmpw[i], kernel)[pix_width:-pix_width]
    tmp = tmp.transpose()
    tmpw = tmpw.transpose()
    for i in range(0, len(tmp)):
        tmp[i] = numpy.convolve(tmp[i], kernel)[pix_width:-pix_width]
        tmpw[i] = numpy.convolve(tmpw[i], kernel)[pix_width:-pix_width]
    tmp = tmp.transpose()
    tmpw = tmpw.transpose()
    tmp[mask_array != 0] = (
        mask_array[mask_array != 0]
        * tmp[mask_array != 0]
        / tmpw[mask_array != 0]
        + (1 - mask_array[mask_array != 0]) * raster[mask_array != 0]
    )
    if preserve_boundary:
        for i in range(pix_width):
            tmp[i] = (
                i / pix_width * tmp[i] + (pix_width - i) / pix_width * raster[i]
            )
            tmp[-i - 1] = (
                i / pix_width * tmp[-i - 1]
                + (pix_width - i) / pix_width * raster[-i - 1]
            )
        for i in range(pix_width):
            tmp[:, i] = (
                i / pix_width * tmp[:, i]
                + (pix_width - i) / pix_width * raster[:, i]
            )
            tmp[:, -i - 1] = (
                i / pix_width * tmp[:, -i - 1]
                + (pix_width - i) / pix_width * raster[:, -i - 1]
            )
    return raster * (mask_array == 0) + tmp * (mask_array != 0)
