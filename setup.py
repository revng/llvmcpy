from setuptools import setup, find_packages
from codecs import open
from os import path

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='llvmcpy',
    version='0.1.0',
    description='Python bindings for LLVM auto-generated from the LLVM-C API',
    long_description=long_description,
    url='https://rev.ng/llvmcpy',
    author='Alessandro Di Federico',
    author_email='ale+llvmcpy@clearmind.me',
    license='MIT',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 3',
    ],
    keywords='llvm',
    packages=['llvmcpy'],
    install_requires=['cffi>=1.0.0'],
    test_suite="llvmcpy.test.TestSuite",
)
