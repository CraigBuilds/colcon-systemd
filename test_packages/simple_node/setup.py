from setuptools import find_packages, setup

package_name = 'simple_node'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(),
    data_files=[
        ('share/' + package_name, []),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='test',
    maintainer_email='test@example.com',
    description='Minimal Python package for colcon-systemd build integration testing',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'simple_node = simple_node.main:main',
        ],
    },
)
