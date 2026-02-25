from setuptools import setup, find_packages

setup(
    name="polymarket-trader",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "requests>=2.31.0",
        "pandas>=2.0.0",
        "tabulate>=0.9.0",
    ],
    entry_points={
        "console_scripts": [
            "polymarket-trader=polymarket_trader.cli:main",
        ],
    },
    python_requires=">=3.10",
)
