import json
import os
import re
from pathlib import Path

import discord
from discord.ext import commands


LANGUAGE_DIR = Path(__file__).resolve().parent.parent / "languages"
DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "es": "Español",
}


def available_languages():
    languages = dict(SUPPORTED_LANGUAGES)
    if LANGUAGE_DIR.exists():
        for path in LANGUAGE_DIR.glob("*.json"):
            languages.setdefault(path.stem.lower(), path.stem.upper())
    return dict(sorted(languages.items()))


class Translator:
    def __init__(self, language_dir=LANGUAGE_DIR):
        self.language_dir = Path(language_dir)
        self.catalogs = {}
        self.template_catalogs = {}
        self.load()

    def load(self):
        self.catalogs.clear()
        self.template_catalogs.clear()
        for language_code in available_languages():
            path = self.language_dir / f"{language_code}.json"
            try:
                with open(path, "r", encoding="utf-8") as file:
                    data = json.load(file)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            catalog = data if isinstance(data, dict) else {}
            self.catalogs[language_code] = catalog
            self.template_catalogs[language_code] = self.build_template_catalog(catalog)

    def build_template_catalog(self, catalog):
        templates = []
        for source, target in catalog.items():
            if "{}" not in source or "{}" not in target:
                continue

            source_parts = source.split("{}")
            pattern = "^" + "(.*?)".join(re.escape(part) for part in source_parts) + "$"
            templates.append((
                source,
                target,
                re.compile(pattern, re.DOTALL),
                len("".join(source_parts)),
            ))

        templates.sort(key=lambda item: item[3], reverse=True)
        return templates

    def normalize_language(self, language_code):
        language_code = str(language_code or DEFAULT_LANGUAGE).strip().lower()
        return language_code if language_code in available_languages() else DEFAULT_LANGUAGE

    def translate(self, text, language_code):
        if text is None:
            return None
        if not isinstance(text, str) or not text:
            return text

        language_code = self.normalize_language(language_code)
        if language_code == DEFAULT_LANGUAGE:
            return text

        translated = self.catalogs.get(language_code, {}).get(text)
        if translated:
            return translated

        template_translated = self.translate_template(text, language_code)
        return template_translated or text

    def translate_template(self, text, language_code):
        for _source, target, pattern, _literal_length in self.template_catalogs.get(language_code, []):
            match = pattern.match(text)
            if not match:
                continue

            values = match.groups()
            if target.count("{}") != len(values):
                continue

            try:
                translated_values = [
                    self.translate_capture(value, language_code)
                    for value in values
                ]
                return target.format(*translated_values)
            except (IndexError, KeyError, ValueError):
                continue

        return None

    def translate_capture(self, value, language_code):
        alpha_count = sum(character.isalpha() for character in value)
        if alpha_count < 4:
            return value
        return self.translate(value, language_code)

    def translate_embed(self, embed, language_code):
        if embed is None:
            return None

        translated = embed.copy()
        translated.title = self.translate(translated.title, language_code)
        translated.description = self.translate(translated.description, language_code)

        for index, field in enumerate(translated.fields):
            translated.set_field_at(
                index,
                name=self.translate(field.name, language_code),
                value=self.translate(field.value, language_code),
                inline=field.inline,
            )

        if translated.footer and translated.footer.text:
            translated.set_footer(
                text=self.translate(translated.footer.text, language_code),
                icon_url=translated.footer.icon_url,
            )

        if translated.author and translated.author.name:
            translated.set_author(
                name=self.translate(translated.author.name, language_code),
                url=translated.author.url,
                icon_url=translated.author.icon_url,
            )

        return translated

    def translate_view(self, view, language_code):
        if view is None:
            return None

        for item in getattr(view, "children", []):
            if getattr(item, "label", None):
                item.label = self.translate(item.label, language_code)
            if getattr(item, "placeholder", None):
                item.placeholder = self.translate(item.placeholder, language_code)
            if getattr(item, "options", None):
                for option in item.options:
                    if getattr(option, "label", None):
                        option.label = self.translate(option.label, language_code)
                    if getattr(option, "description", None):
                        option.description = self.translate(option.description, language_code)

        return view

    def translate_modal(self, modal, language_code):
        if modal is None:
            return None

        if getattr(modal, "title", None):
            modal.title = self.translate(modal.title, language_code)
        for item in getattr(modal, "children", []):
            if getattr(item, "label", None):
                item.label = self.translate(item.label, language_code)
            if getattr(item, "placeholder", None):
                item.placeholder = self.translate(item.placeholder, language_code)

        return modal

    def translate_kwargs(self, kwargs, language_code):
        localized = dict(kwargs)
        if "content" in localized:
            localized["content"] = self.translate(localized.get("content"), language_code)
        if "embed" in localized:
            localized["embed"] = self.translate_embed(localized.get("embed"), language_code)
        if "embeds" in localized and localized.get("embeds") is not None:
            localized["embeds"] = [
                self.translate_embed(embed, language_code)
                for embed in localized["embeds"]
            ]
        if "view" in localized:
            localized["view"] = self.translate_view(localized.get("view"), language_code)
        return localized


translator = Translator()


def language_for_user(bot, user):
    if user is None:
        return DEFAULT_LANGUAGE

    users_cog = bot.get_cog("Users") if bot else None
    if users_cog is None:
        return DEFAULT_LANGUAGE

    try:
        profile = users_cog.users.get(str(user.id), {})
        settings = users_cog.normalize_settings(profile)
    except Exception:
        return DEFAULT_LANGUAGE

    return translator.normalize_language(settings.get("language"))


def t(text, language_code=DEFAULT_LANGUAGE):
    return translator.translate(text, language_code)


def localize_embed(embed, language_code=DEFAULT_LANGUAGE):
    return translator.translate_embed(embed, language_code)


def install_discord_translation(bot):
    if getattr(bot, "_propulse_i18n_installed", False):
        return

    original_context_send = commands.Context.send
    original_response_send_message = discord.InteractionResponse.send_message
    original_response_edit_message = discord.InteractionResponse.edit_message
    original_response_send_modal = discord.InteractionResponse.send_modal

    async def localized_context_send(ctx, *args, **kwargs):
        language_code = language_for_user(ctx.bot, getattr(ctx, "author", None))
        if args:
            first, *rest = args
            kwargs.setdefault("content", first)
            args = tuple(rest)
        kwargs = translator.translate_kwargs(kwargs, language_code)
        return await original_context_send(ctx, *args, **kwargs)

    async def localized_response_send_message(response, *args, **kwargs):
        interaction = getattr(response, "_parent", None)
        language_code = language_for_user(bot, getattr(interaction, "user", None))
        if args:
            first, *rest = args
            kwargs.setdefault("content", first)
            args = tuple(rest)
        kwargs = translator.translate_kwargs(kwargs, language_code)
        return await original_response_send_message(response, *args, **kwargs)

    async def localized_response_edit_message(response, *args, **kwargs):
        interaction = getattr(response, "_parent", None)
        language_code = language_for_user(bot, getattr(interaction, "user", None))
        if args:
            first, *rest = args
            kwargs.setdefault("content", first)
            args = tuple(rest)
        kwargs = translator.translate_kwargs(kwargs, language_code)
        return await original_response_edit_message(response, *args, **kwargs)

    async def localized_response_send_modal(response, modal, *args, **kwargs):
        interaction = getattr(response, "_parent", None)
        language_code = language_for_user(bot, getattr(interaction, "user", None))
        modal = translator.translate_modal(modal, language_code)
        return await original_response_send_modal(response, modal, *args, **kwargs)

    commands.Context.send = localized_context_send
    discord.InteractionResponse.send_message = localized_response_send_message
    discord.InteractionResponse.edit_message = localized_response_edit_message
    discord.InteractionResponse.send_modal = localized_response_send_modal
    bot._propulse_i18n_installed = True


def amazon_translate_text(text, target_language_code, source_language_code="en"):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("Install boto3 to use Amazon Translate.") from exc

    region_name = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    client = boto3.client("translate", region_name=region_name)
    result = client.translate_text(
        Text=text,
        SourceLanguageCode=source_language_code,
        TargetLanguageCode=target_language_code,
    )
    return result["TranslatedText"]
