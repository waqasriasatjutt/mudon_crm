# -*- coding: utf-8 -*-
"""Rebrand the Odoo 19 PWA as the *Mudon CRM* app.

Odoo serves a Web App Manifest at /web/manifest.webmanifest that turns the
backend into an installable Progressive Web App (Add to Home Screen → opens
full-screen like a native app). We override the manifest builder to swap in
Mudon's name, icons, theme colour and a start page that lands on the CRM.
"""
from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.webmanifest import WebManifest

MUDON_NAVY = "#1b2b44"
_ICON = "/mudon_pwa/static/img/%s"


class WebManifest(WebManifest):

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        icp = request.env["ir.config_parameter"].sudo()
        manifest["name"] = icp.get_param("web.web_app_name", "Mudon CRM") \
            or "Mudon CRM"
        manifest["short_name"] = "Mudon"
        manifest["description"] = "Mudon Property — CRM for Turkey CBI & " \
            "UAE Dubai Golden-Visa pipelines."
        manifest["background_color"] = MUDON_NAVY
        manifest["theme_color"] = MUDON_NAVY
        manifest["icons"] = [
            {"src": _ICON % "mudon-icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": _ICON % "mudon-icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any"},
            {"src": _ICON % "mudon-icon-maskable-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ]
        # Land the installed app straight on the Mudon CRM pipeline instead of
        # the generic app switcher (falls back to /odoo if the menu is absent).
        menu = request.env.ref("mudon_crm.mudon_menu_app_root",
                               raise_if_not_found=False)
        if menu:
            manifest["start_url"] = "/odoo?menu_id=%s" % menu.id
        return manifest

    def _icon_path(self):
        # Branded offline-page / fallback icon.
        return "mudon_pwa/static/img/mudon-icon-192.png"
