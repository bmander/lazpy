from setuptools import setup, Extension

cpylaz = Extension(
    'cpylaz',
    sources=[
        'cpylazmodule.c',
        'src/laz_stream.c',
        'src/laz_arithmetic.c',
        'src/laz_intcompressor.c',
        'src/laz_readitem_raw.c',
        'src/laz_readitem_v1.c',
        'src/laz_readitem_v2.c',
        'src/laz_readitem_v3.c',
        'src/laz_readpoint.c',
    ],
    include_dirs=['src'],
    extra_compile_args=['-O2', '-Wall', '-Wextra'],
)

setup(name="lazpy",
      version="0.2",
      description="Python LAZ reader",
      author="Brandon Martin-Anderson",
      py_modules=["lazpy", "models", "encoder", "compressor", "utils"],
      ext_modules=[cpylaz])
