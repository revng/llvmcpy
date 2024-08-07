from codecs import open
from os import path

from setuptools import find_packages, setup

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="llvmcpy",
    version="0.1.6",
    description="Python bindings for LLVM auto-generated from the LLVM-C API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/revng/llvmcpy",
    author="Alessandro Di Federico",
    author_email="ale.llvmcpy@clearmind.me",
    license="MIT",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
    ],
    keywords="llvm",
    packages=["llvmcpy"],
    install_requires=["cffi>=1.0.0", "pycparser", "appdirs", "packaging"],
    test_suite="llvmcpy.test.TestSuite",
)
