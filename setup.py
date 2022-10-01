from distutils.core import setup, Extension

cmodels = Extension('cmodels', sources=['cmodels.c'])

setup(name="lazpy",
      version="0.1",
      description="Python LAZ reader",
      author="Brandon Martin-Anderson",
      ext_modules=[cmodels])
