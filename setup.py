# setup.py
from setuptools import setup, find_packages

setup(
    name="ssim_video_optimizer",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "ssim-video-optimizer=ssim_video_optimizer.cli:main"
        ]
    },
)
