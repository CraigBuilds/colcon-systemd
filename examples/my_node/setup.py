from setuptools import find_packages, setup

package_name = 'my_node'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(),
    data_files=[
        # Creates share/my_node/ in the install tree (required by colcon)
        ('share/' + package_name, []),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='example',
    maintainer_email='example@example.com',
    description='Example package for colcon-systemd',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'my_node = my_node.main:main',
        ],
    },
)
