# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TrendRadar
# Usage: pyinstaller TrendRadar.spec --clean --noconfirm

import os
from PyInstaller.building.build_main import Analysis, PYZ, EXE, BUNDLE, COLLECT

block_cipher = None

# Project root
project_root = os.path.abspath(os.path.dirname(SPECPATH))

a = Analysis(
    ['main.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        # HTML templates used by Jinja2
        ('templates', 'templates'),
        # Default config files
        ('config', 'config'),
        # Static HTML report viewer
        ('index.html', '.'),
        # Version file
        ('version', '.'),
        # Core Python modules
        ('trendradar', 'trendradar'),
        ('mcp_server', 'mcp_server'),
    ],
    hiddenimports=[
        # Core dependencies
        'requests',
        'requests.adapters',
        'requests.packages.urllib3',
        'pytz',
        'pytz.zoneinfo',
        'yaml',
        'jinja2',
        'jinja2.ext',
        'fastmcp',
        'fastmcp.server',
        'websockets',
        'websockets.legacy',
        'websockets.legacy.server',
        'structlog',
        'pydantic',
        'pydantic_settings',
        'pydantic_core',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'email.mime.text',
        'email.mime.multipart',
        'email.mime.base',
        'smtplib',
        'ssl',
        # MCP server internal modules
        'mcp_server.tools.data_query',
        'mcp_server.tools.analytics',
        'mcp_server.tools.search_tools',
        'mcp_server.tools.config_mgmt',
        'mcp_server.tools.system',
        'mcp_server.services.data_service',
        'mcp_server.services.parser_service',
        'mcp_server.services.cache_service',
        'mcp_server.utils.date_parser',
        'mcp_server.utils.errors',
        'mcp_server.utils.validators',
        # TrendRadar internal modules
        'trendradar.config',
        'trendradar.fetcher',
        'trendradar.notifier',
        'trendradar.notifier.base',
        'trendradar.notifier.email',
        'trendradar.notifier.ntfy',
        'trendradar.records',
        'trendradar.utils',
        'trendradar.logging_config',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy/unnecessary packages to reduce bundle size
        'matplotlib',
        'numpy',
        'pandas',
        'tkinter',
        'PIL',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
        'scipy',
        'sklearn',
        'tensorflow',
        'torch',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TrendRadar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=True for CLI tool that may print logs
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name='TrendRadar.app',
    icon=None,
    bundle_identifier='com.arronai.trendradar',
    info_plist={
        'CFBundleDisplayName': 'TrendRadar',
        'CFBundleExecutable': 'TrendRadar',
        'CFBundleIdentifier': 'com.arronai.trendradar',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleName': 'TrendRadar',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': '3.5.0',
        'CFBundleVersion': '3.5.0',
        'LSMinimumSystemVersion': '10.15',
        'NSHighResolutionCapable': True,
    },
)
