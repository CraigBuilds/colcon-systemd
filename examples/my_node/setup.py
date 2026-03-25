from setuptools import find_packages, setup

package_name = 'my_node'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='example',
    maintainer_email='example@example.com',
    description='Example minimal ROS 2 publisher node managed by colcon-systemd',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'my_node = my_node.main:main',
        ],
    },
)
