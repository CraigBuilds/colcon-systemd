from setuptools import find_packages, setup

package_name = 'simple_node'

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
    maintainer='test',
    maintainer_email='test@example.com',
    description='Minimal ROS 2 node for colcon-systemd build integration testing',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'simple_node = simple_node.main:main',
        ],
    },
)
