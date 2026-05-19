import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from classes.i18n import translator
import discord


SAMPLES = {
    "Language set to Español.": "Idioma configurado en Español.",
    "You already claimed the reward for your latest Top.gg vote. You can vote again in 2h 3m.": (
        "Ya reclamaste la recompensa por tu último voto en Top.gg. Puedes votar nuevamente en 2h 3m."
    ),
    "⏳ Wait 4m 12s before practicing again.": "⏳ Esperar 4m 12s antes de practicar de nuevo.",
    "Please wait 12s before confirming.": "Espere por favor 12s antes de confirmar.",
}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    failures = []
    for source, expected in SAMPLES.items():
        actual = translator.translate(source, "es")
        if actual != expected:
            failures.append((source, expected, actual))

    if failures:
        for source, expected, actual in failures:
            print(f"Source:   {source}")
            print(f"Expected: {expected}")
            print(f"Actual:   {actual}")
            print()
        raise SystemExit(1)

    print(f"Runtime i18n checks passed for {len(SAMPLES)} samples.")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Confirm", style=discord.ButtonStyle.green))
    translator.translate_view(view, "es")
    if view.children[0].label != "Confirmar":
        raise SystemExit(f"View label translation failed: {view.children[0].label}")

    class ExampleModal(discord.ui.Modal, title="Add Card"):
        value = discord.ui.TextInput(label="Inventory Index")

    modal = ExampleModal()
    translator.translate_modal(modal, "es")
    if modal.title != "Agregar tarjeta" or modal.children[0].label != "Índice de inventario":
        raise SystemExit(
            f"Modal translation failed: {modal.title}, {modal.children[0].label}"
        )

    print("Component i18n checks passed.")


if __name__ == "__main__":
    main()
