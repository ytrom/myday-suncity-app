class BowlControlRouter:
    """Send the `bc` app (BowlControl tables) to the bowlcontrol database; never migrate it."""

    app_label = "bc"
    db = "bowlcontrol"

    def db_for_read(self, model, **hints):
        return self.db if model._meta.app_label == self.app_label else None

    def db_for_write(self, model, **hints):
        return self.db if model._meta.app_label == self.app_label else None

    def allow_relation(self, obj1, obj2, **hints):
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        if labels == {self.app_label}:
            return True
        if self.app_label in labels:
            return False
        return None

    def allow_migrate(self, db, app_label, **hints):
        if db == self.db:
            return False
        if app_label == self.app_label:
            return False
        return None
