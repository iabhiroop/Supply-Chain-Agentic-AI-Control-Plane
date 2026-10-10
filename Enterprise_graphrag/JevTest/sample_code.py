"""Sample snippet for Jev code review."""


def divide(a, b):
    return a / b


def load_user(db, user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return db.execute(query)
