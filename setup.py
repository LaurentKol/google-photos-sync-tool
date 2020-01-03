# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='google_photos_sync_tool',
    version='0.1.0',
    description='Google Photos Sync tool',
    long_description=readme,
    author='Laurent Kol',
    author_email='laurent.kol@gmail.com',
    url='https://github.com/LaurentKol/google-photos-sync-tool',
    license=license,
    install_requires=requirements,
    data_files=['albums.yaml'],
    package_dir={"": "src"},
    packages=find_packages(where="src", exclude=('tests', 'docs')),
    entry_points={
        "console_scripts": [
            "google_photos_sync_tool=google_photos_sync_tool.__main__:main",
        ],
    }
)
