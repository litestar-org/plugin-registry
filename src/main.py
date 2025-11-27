import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml
import glob
import msgspec
import structlog
import json

from datetime import datetime

from src.types import PluginInfo
from src.utils import fetch_pypi, fetch_pypistats, get_github_stars_auth, parse_requires_python, get_github_readme
from src.constants import (
    PLUGINS_DIR,
    ROOT_DIR,
    READMES_DIR,
    OUTPUT_DIR,
    DEFAULT_PLUGIN_ICON,
)

logger = structlog.get_logger()


async def sync(key: str) -> PluginInfo:
    """Sync plugin information from various sources."""
    plugin: PluginInfo | None = await get_plugin(key)

    if not plugin:
        await logger.aerror("Plugin not found", plugin_key=key)
        raise ValueError(f"Plugin with key {key} not found")

    # Validate and assign icon
    if plugin.icon:
        icon_file: Path = ROOT_DIR.joinpath("icons", plugin.icon)
        if not icon_file.exists():
            await logger.aerror(f"Icon {plugin.icon} does not exist for {plugin.key}")
            raise ValueError(f"Icon {plugin.icon} does not exist for {plugin.key}")
    else:
        # Assign default icon if none provided
        plugin.icon = DEFAULT_PLUGIN_ICON
        await logger.awarning(
            f"Assigned default icon to plugin {plugin.name}", icon=plugin.icon
        )

    # Retrieves github starts
    if plugin.repo:
        stars: int | None = await get_github_stars_auth(plugin.repo)
        if stars is not None:
            plugin.stars = stars

    # Retrieves data from PyPI
    if plugin.pypi:
        pypi_data: dict[str, Any] = await fetch_pypi(plugin.pypi)
        if pypi_data:
            info: Any = pypi_data.get("info", {})
            releases: Any = pypi_data.get("releases", {})
            
            # Get version
            plugin.latest_version = info.get("version")
            
            # Get python compatibility
            plugin.python_compatibility_raw = info.get("requires_python")
            plugin.python_compatibility = parse_requires_python(plugin.python_compatibility_raw)

            # Get changelog and issues from project_urls
            project_urls: Dict = info.get("project_urls", {})
            if project_urls:
                plugin.changelog = project_urls.get("Changelog")
                plugin.issues = project_urls.get("Issue")
            
            # Get created_at and updated_at from releases
            if releases:
                upload_times: list[datetime] = []
                for version_releases in releases.values():
                    for release in version_releases:
                        if upload_time := release.get("upload_time"):
                            try:
                                upload_times.append(datetime.fromisoformat(upload_time.replace('Z', '+00:00')))
                            except (ValueError, AttributeError):
                                continue
                
                if upload_times:
                    upload_times.sort()
                    plugin.created_at = upload_times[0]  # Oldest
                    plugin.updated_at = upload_times[-1]  # Newest
        
    # Retrieves data from PyPI Stats
    if plugin.pypi:
        pypistats_data = await fetch_pypistats(plugin.pypi)
        if pypistats_data:
            data: Any = pypistats_data.get("data", {})
            if data:
                plugin.monthly_downloads = data.get("last_month")

    # Convert to dict to manipulate fields
    plugin_dict = msgspec.to_builtins(plugin)

    # Reconstruct module from cleaned dict
    plugin = msgspec.convert(plugin_dict, PluginInfo)

    # Write plugin
    await write_plugin(plugin)

    return plugin


async def get_plugin(key: str) -> PluginInfo | None:
    """Get plugin configuration."""

    # Load existing configuration if it exists
    file_path: Path = PLUGINS_DIR.joinpath(f"{key}.yml")
    if file_path.exists():
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return msgspec.convert(data, PluginInfo)

    return None


async def write_plugin(plugin: PluginInfo) -> None:
    """Write plugin configuration to YAML file."""
    file_path: Path = PLUGINS_DIR.joinpath(f"{plugin.key}.yml")

    # Convert to dict for YAML serialization
    plugin_dict = msgspec.to_builtins(plugin)

    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(plugin_dict, f, default_flow_style=False, sort_keys=False)


async def read_plugins() -> List[PluginInfo]:
    """Read all plugin configurations."""
    pattern = str(PLUGINS_DIR / "*.yml")
    files = glob.glob(pattern)

    names = [
        Path(f).stem
        for f in files
        if Path(f).stem  # Filter out empty names
    ]

    tasks = [get_plugin(name) for name in names]
    plugins = await asyncio.gather(*tasks)

    return [p for p in plugins if p.name]


async def sync_all() -> Dict[str, Any]:
    """Sync all plugins with rate limiting."""
    plugins: list[PluginInfo] = await read_plugins()
    success: bool = True

    # Rate limiting using semaphore (equivalent to pLimit(10))
    semaphore: asyncio.Semaphore = asyncio.Semaphore(10)

    async def sync_with_limit(plugin: PluginInfo) -> Optional[PluginInfo]:
        async with semaphore:
            try:
                await logger.ainfo(f"Syncing plugin {plugin.key}")
                return await sync(plugin.key)
            except Exception as err:
                await logger.aerror(
                    f"Error syncing plugin {plugin.key}", error=str(err)
                )
                return None

    tasks = [sync_with_limit(plugin) for plugin in plugins]
    updated_plugins = await asyncio.gather(*tasks, return_exceptions=True)

    return {"count": len(updated_plugins), "success": success}


async def get_readme() -> None:
    """Fetch README files for all plugins."""
    await logger.ainfo("Get README process started")

    plugins: list[PluginInfo] = await read_plugins()
    READMES_DIR.mkdir(exist_ok=True)
    
    # Rate limiting using semaphore
    semaphore: asyncio.Semaphore = asyncio.Semaphore(10)

    async def fetch_readme_with_limit(plugin: PluginInfo) -> Optional[str]:
        async with semaphore:
            try:
                await logger.ainfo(f"Fetching README for plugin {plugin.key}")
                
                readme_content: str | None = await get_github_readme(plugin.repo)
                if readme_content:
                    readme_file: Path = READMES_DIR.joinpath(f"{plugin.key}.md")
                    
                    with open(readme_file, "w", encoding="utf-8") as f:
                        f.write(readme_content)
                    
                    await logger.ainfo(f"README saved for {plugin.key}")
                    return readme_content
                else:
                    await logger.awarning(f"No README found for {plugin.key}")
                    return None
                
            except Exception as err:
                await logger.aerror(f"Error fetching README for {plugin.key}", error=str(err))
                return None
    
    tasks = [fetch_readme_with_limit(plugin) for plugin in plugins]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success_count: int = sum(1 for r in results if r is not None and not isinstance(r, Exception))
    
    await logger.ainfo(
        "Get README process completed.", count=success_count, total=len(plugins)
    )


def get_readme_cli() -> None:
    """CLI wrapper for the get-readme command."""
    asyncio.run(get_readme())


def sync_all_cli() -> None:
    """CLI wrapper for the sync_all command."""
    logger.info("Starting sync all")
    result = asyncio.run(sync_all())
    logger.info("Sync all completed", count=result["count"], success=result["success"])


async def consolidate_plugins() -> None:
    """Consolidate all individual plugin YAML files into a single plugins.yml file."""
    await logger.ainfo("Consolidating plugins into single file")

    plugins: list[PluginInfo] = await read_plugins()

    # Convert all plugins to dict format
    plugins_data = {}
    for plugin in plugins:
        plugin_dict = msgspec.to_builtins(plugin)
        plugins_data[plugin.key] = plugin_dict

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Write consolidated plugins.yml
    output_file: Path = OUTPUT_DIR.joinpath("plugins.yml")
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.dump(plugins_data, f, default_flow_style=False, sort_keys=False)

    await logger.ainfo(f"Consolidated {len(plugins)} plugins into output/plugins.yml")


async def consolidate_readmes() -> None:
    """Consolidate all individual README markdown files into a single readmes.json file."""

    await logger.ainfo("Consolidating READMEs into single JSON file")

    readmes_data = {}
    readme_files = list(READMES_DIR.glob("*.md"))

    for readme_file in readme_files:
        if readme_file.name == ".gitkeep":
            continue

        plugin_key = readme_file.stem

        with open(readme_file, "r", encoding="utf-8") as f:
            content = f.read()
            readmes_data[plugin_key] = content

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Write consolidated readmes.json
    output_file: Path = OUTPUT_DIR.joinpath("readmes.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(readmes_data, f, indent=2, ensure_ascii=False)

    await logger.ainfo(f"Consolidated {len(readmes_data)} READMEs into output/readmes.json")


def consolidate_plugins_cli() -> None:
    """CLI wrapper for the consolidate-plugins command."""
    asyncio.run(consolidate_plugins())


def consolidate_readmes_cli() -> None:
    """CLI wrapper for the consolidate-readmes command."""
    asyncio.run(consolidate_readmes())
