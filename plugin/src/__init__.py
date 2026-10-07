"""Package marker so ``src.ram_first_tool`` resolves when the distribution is
installed (entry-point discovery). The directory-copy install path does not
need this file (Python's namespace packages resolve ``src`` from the plugin
directory), but the published repo ships it so both install paths behave
identically."""
