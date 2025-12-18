from __future__ import annotations
from quantbot.storage.db import engine
from quantbot.storage.models import Base

def main():
    Base.metadata.create_all(bind=engine)
    print("DB schema created.")

if __name__ == "__main__":
    main()
