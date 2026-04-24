import os
import sys
sys.path.insert(0, os.path.abspath('../'))

project = 'NYC Cabs Experimental Platform'
author = 'Andrew Milton'
release = '0.1.0'

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
]

html_theme = 'sphinx_rtd_theme'
