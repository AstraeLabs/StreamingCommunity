# 18.08.24

import os
import re
from setuptools import setup, find_packages

# Variable
base_path = os.path.abspath(os.path.dirname(__file__))

def read_readme():
    readme_path = os.path.join(base_path, "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as fh:
            return fh.read()

def read_requirements():
    req_path = os.path.join(base_path, "requirements.txt")
    if os.path.exists(req_path):
        with open(req_path, "r", encoding="utf-8-sig") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def get_version():
    try:
        import pkg_resources
        return pkg_resources.get_distribution('VibraVid').version
    except Exception:
        version_file_path = os.path.join(os.path.dirname(__file__), "VibraVid", "upload", "version.py")
        with open(version_file_path, "r", encoding="utf-8") as f:
            version_match = re.search(r"^__version__\s*=\s*['\"]([^'\"]*)['\"]$", f.read(), re.M)
        if version_match:
            return version_match.group(1)
        raise RuntimeError("Unable to find version string in VibraVid/upload/version.py.")

def get_package_data_files(directory):
    """Get all .py files in the specified directory and its subdirectories."""
    paths = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                rel_dir = os.path.relpath(root, directory)
                if rel_dir == '.':
                    paths.append(file)
                else:
                    paths.append(os.path.join(rel_dir, file))
    return paths

setup(
    name="VibraVid",
    version=get_version(),
    description="Download content from streaming platforms",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    author="Arrowar",
    url="https://github.com/AstraeLabs/VibraVid",
    
    packages=find_packages(
        exclude=["tests", "tests.*", "docs", "docs.*", "GUI", "GUI.*", "Test", "Test.*"]
    ),
    
    install_requires=read_requirements(),
    python_requires='>=3.8',
    
    entry_points={
        "console_scripts": [
            "VibraVid=VibraVid.cli.run:main",
        ],
    },
    
    include_package_data=True,
    
    package_data={
        '': ['*.txt', '*.md', '*.json', '*.yaml', '*.yml', '*.cfg'],
        'VibraVid': [
            '**/*.txt', '**/*.json', '**/*.yaml',
            'cli/**/*.py',
            'core/**/*.py',
            'player/**/*.py',
            'services/**/*.py',
            'services/**/util/*.py',
            'setup/**/*.py',
            'source/**/*.py',
            'upload/**/*.py',
            'utils/**/*.py',
        ],
    },
    
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    
    project_urls={
        "Bug Reports": "https://github.com/AstraeLabs/VibraVid/issues",
        "Source": "https://github.com/AstraeLabs/VibraVid",
    }
)