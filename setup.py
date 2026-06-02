from setuptools import find_packages, setup

setup(
    name="agent-acceptance-trace",
    version="0.1.0",
    description="Trace coding-agent acceptance criteria to diff and closeout evidence.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="Manuel Sampedro",
    license="MIT",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.9",
    entry_points={"console_scripts": ["agent-acceptance-trace=agent_acceptance_trace.cli:main"]},
)
