"""Initial training plan that is created on first setup."""

from __future__ import annotations

from typing import Any

LEGS = "Beine & Rumpf"
UPPER = "Oberkörper"
ARMS = "Arme"


def _ex(
    name: str,
    group: str,
    sets: int,
    reps: tuple[int, int] | None,
    weight: float | None,
    setting: str = "",
    hint: str = "",
    duration: int | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "muscle_group": group,
        "target_sets": sets,
        "reps_min": reps[0] if reps else None,
        "reps_max": reps[1] if reps else None,
        "duration_s": duration,
        "weighted": weight is not None,
        "start_weight": weight,
        "setting": setting,
        "hint": hint,
    }


DEFAULT_PLAN: dict[str, Any] = {
    "name": "Ganzkörper-Training",
    "freq_min": 2,
    "freq_max": 3,
    "warmup": "Ausfallschritte + Rotation, 5–10 Wdh. pro Seite",
    "exercises": [
        _ex(
            "Beinpresse", LEGS, 3, (12, 14), 63, "Sitz 6–7",
            "Schulterbreiter Stand, Füße leicht nach außen rotiert, Knie in dieselbe Richtung. "
            "Knie nicht komplett durchstrecken.",
        ),
        _ex(
            "Beinbeuger (liegend)", LEGS, 3, (12, 14), 27, "Gelenk 4, Rolle 1",
            "Knie auf Höhe des Maschinengelenks. Füße zeigen Richtung Knie.",
        ),
        _ex(
            "Rückenstrecker", LEGS, 3, (10, 12), None, "Polster 5",
            "Hüftgelenk über dem Polster. Nur über die Hüfte beugen und strecken („Klappmesser“).",
        ),
        _ex("Plank", LEGS, 3, None, None, "", "Ggf. Ellbogen weiter nach vorne platzieren.", duration=30),
        _ex(
            "Rudern (brustgestützt)", UPPER, 3, (12, 14), 32, "Brustpolster 2, Sitz 6",
            "Schulterblätter nach vorne mitwandern lassen, dann aufrichten und Schulterblätter "
            "fest zusammenziehen.",
        ),
        _ex(
            "Latzug (Kabelzug)", UPPER, 3, (12, 14), 32, "Enger Griff",
            "Zur Brust ziehen, Oberkörper leicht nach hinten. Ellbogen Richtung Hüfte ziehen.",
        ),
        _ex(
            "Brustpresse", UPPER, 3, (12, 14), 25, "Sitz so, dass Schultern oberhalb der Griffe sind",
            "Stolze Brust, Schulterblätter am Polster fixiert, Gewicht nach vorne drücken.",
        ),
        _ex(
            "Seitheben mit Hanteln", UPPER, 2, (15, 15), 3, "",
            "Oberkörper leicht nach vorne neigen, Arme seitlich abspreizen, mit den Ellbogen führen.",
        ),
        _ex("Bizeps Curls", ARMS, 2, (15, 15), 18, "Maschine"),
        _ex("Trizeps Extensions", ARMS, 2, (15, 15), 14, "Seil"),
    ],
}
