from setuptools import find_namespace_packages, setup


setup(
    packages=find_namespace_packages("src", exclude=["*.ipynb_checkpoints*"])
    + ["ndlocr_lite_gui"],
)
