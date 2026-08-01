"""Fetch both model packages into EARTH2STUDIO_CACHE at image-build time.

load_default_package() resolves and downloads the NGC packages (SFNO 6.87 GiB,
CorrDiffTaiwan 684 MiB) without needing a GPU — loading the weights onto a device
happens at run time. Baking them into the image means a rented instance never
spends paid minutes waiting on NGC.
"""

from earth2studio.models.dx import CorrDiffTaiwan
from earth2studio.models.px import SFNO

SFNO.load_default_package()
CorrDiffTaiwan.load_default_package()
print("model packages baked into image cache")
