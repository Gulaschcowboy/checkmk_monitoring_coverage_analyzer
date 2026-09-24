# Default-Werte fuer die beiden globalen Optionen aus
# plugins/wato/monitoring_coverage_analyzer_globals.py.
#
# Checkmk liest Default-Werte fuer ConfigVariable-Eintraege NICHT aus der
# ConfigVariable selbst, sondern aus dem "config"-Legacy-Plugin-Namespace
# (siehe cmk.gui.config._get_default_config_from_legacy_plugins() ->
# utils.load_web_plugins("config", default_config) - live auf
# Test-Site/Checkmk 2.5.0p12 Ultimate per grep verifiziert): jedes
# Modul-Attribut hier mit demselben Namen wie ein ConfigVariable-ident
# wird zum Default-Wert dieser Variable, solange sie noch nicht explizit
# in multisite.mk/multisite.d/*.mk gesetzt wurde.
#
# WICHTIGER FALLSTRICK (live auf der Test-Site entdeckt und verifiziert):
# load_web_plugins("config", default_config) fuehrt diese Datei per
# exec(compile(...), default_config) aus, d.h. JEDES Modul-Attribut
# landet als Schluessel in default_config - inklusive eines
# Modul-DOCSTRINGS, der als "__doc__"-Eintrag zum vermeintlichen
# "custom config key" wird! cmk.gui.config.make_config_object() baut
# daraufhin per dataclasses.make_dataclass() eine "ExtendedConfig"-Klasse
# mit einem Feld "__doc__" - was mit
# "TypeError: cannot delete '__doc__' attribute of immutable type
# 'ExtendedConfig'" abstuerzt (dataclasses.py versucht das automatisch
# vom class-Objekt geerbte __doc__-Attribut zu ueberschreiben, was bei
# einem per make_dataclass() erzeugten Typ nicht erlaubt ist). Deshalb
# bewusst KEIN Modul-Docstring in dieser Datei, nur normale Kommentare.
from __future__ import annotations

generate_piggyback_data = True
piggyback_interval_hours = 24
