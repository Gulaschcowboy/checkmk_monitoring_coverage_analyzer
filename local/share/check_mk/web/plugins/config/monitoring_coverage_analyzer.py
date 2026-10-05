# Copyright (C) 2026 Alexander Wilms, Christian Wirtz
# SPDX-License-Identifier: GPL-2.0-only
# Default values for the two global options from
# plugins/wato/monitoring_coverage_analyzer_globals.py.
#
# Checkmk does NOT read default values for ConfigVariable entries from the
# ConfigVariable itself, but from the "config" legacy plugin namespace
# (see cmk.gui.config._get_default_config_from_legacy_plugins() ->
# utils.load_web_plugins("config", default_config), Checkmk 2.5): every
# module attribute here
# with the same name as a ConfigVariable ident becomes the default value
# of that variable, as long as it has not been set explicitly in
# multisite.mk/multisite.d/*.mk.
#
# IMPORTANT PITFALL:
# load_web_plugins("config", default_config) executes this file via
# exec(compile(...), default_config), i.e. EVERY module attribute ends up
# as a key in default_config - including a module DOCSTRING, which turns
# into a supposed "custom config key" as a "__doc__" entry!
# cmk.gui.config.make_config_object() then builds an "ExtendedConfig"
# class via dataclasses.make_dataclass() with a field "__doc__" - which
# crashes with
# "TypeError: cannot delete '__doc__' attribute of immutable type
# 'ExtendedConfig'" (dataclasses.py tries to overwrite the __doc__
# attribute automatically inherited from the class object, which is not
# allowed for a type created via make_dataclass()). Therefore there is
# deliberately NO module docstring in this file, only regular comments.
from __future__ import annotations

generate_piggyback_data = True
piggyback_interval_hours = 24
