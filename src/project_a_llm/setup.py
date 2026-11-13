from setuptools import find_packages, setup

package_name = 'project_a_llm'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    package_data={package_name: ["console.html"]},
    zip_safe=False,
    maintainer='root',
    maintainer_email='root@todo.todo',
    description='TODO: Package description',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'plan = project_a_llm.plan:main',
            'inventory = project_a_llm.inventory:main',
            'state = project_a_llm.state_node:main',
            'monitor = project_a_llm.monitor_node:main',
            'admin = project_a_llm.admin_node:main',
            'microphone = project_a_llm.microphone_node:main',
            'mock_speech = project_a_llm.mock_speech:main',
            'agent = project_a_llm.agent_node:main'
        ],
    },
)





