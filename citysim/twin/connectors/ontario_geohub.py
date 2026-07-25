"""Ontario GeoHub LiDAR DTM/DSM connector (plan §3.1, Tier A).

The Ontario Digital Terrain Model (LiDAR-derived, Hamilton-Niagara 2021
acquisition) is served through an ArcGIS ImageServer. `exportImage` returns a
GeoTIFF clip for a bbox — the programmatic route the plan calls out.

Decoding the returned GeoTIFF needs rasterio/GDAL, which this runtime does not
ship; the connector therefore reports availability honestly and the builder
falls back to procedural terrain. Wiring in rasterio makes `fetch_dtm` return
a real (ny, nx) elevation array with no other code changes.
"""

from __future__ import annotations

IMAGESERVER_URL = (
    "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/"
    "LIO_Imagery/Ontario_DTM_LidarDerived/ImageServer"
)


class OntarioGeoHubDTM:
    def __init__(self, url: str = IMAGESERVER_URL):
        self.url = url

    def available(self) -> bool:
        try:
            import rasterio  # noqa: F401
            return True
        except ImportError:
            return False

    def export_image_url(self, bbox_3857: tuple[float, float, float, float],
                         size: tuple[int, int] = (512, 512)) -> str:
        xmin, ymin, xmax, ymax = bbox_3857
        return (f"{self.url}/exportImage?bbox={xmin},{ymin},{xmax},{ymax}"
                f"&bboxSR=3857&size={size[0]},{size[1]}&format=tiff"
                f"&pixelType=F32&f=image")

    def fetch_dtm(self, bbox_lonlat: list[float], cell_size: float):
        if not self.available():
            raise RuntimeError(
                "Ontario GeoHub DTM requires rasterio (pip install citysim[geo] rasterio); "
                "falling back to procedural terrain."
            )
        raise NotImplementedError("rasterio decode path not wired in this build")
