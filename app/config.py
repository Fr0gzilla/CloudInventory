import os

DEFAULT_DATABASE_URL = "sqlite:///cloudinventory.db"

# Valeurs d'exemple refusées (cf. CAHIER_DES_CHARGES.md : refus si absent ou valeur d'exemple)
REJECTED_VALUES = {
    "",
    "change-me",
    "changeme",
    "dev-only-insecure-key",
    "replace-me",
}


class Config:
    """Configuration centralisée, lue dans l'environnement (.env chargé par create_app)."""

    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REQUIRED_SECRETS = ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")

    @classmethod
    def validate(cls) -> None:
        """Lève RuntimeError si un secret obligatoire est absent, vide ou une valeur d'exemple."""
        problems = []
        for name in cls.REQUIRED_SECRETS:
            value = os.getenv(name)
            if value is None or value.strip().lower() in REJECTED_VALUES:
                problems.append(name)
                continue
            setattr(cls, name, value.strip())
        if problems:
            raise RuntimeError(
                "Configuration invalide : variables d'environnement manquantes ou "
                "valuées par défaut — " + ", ".join(problems) +
                ". Renseignez-les (cf. .env.example) avant de démarrer."
            )
        cls.SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
