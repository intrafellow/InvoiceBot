from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from datetime import datetime

DATABASE_URL = "sqlite:///./tests.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(String, unique=True)
    full_name = Column(String)
    registered_at = Column(DateTime, default=datetime.utcnow)
    templates = relationship("Template", back_populates="user")


class Template(Base):
    __tablename__ = "templates"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    file_path = Column(String)
    ttf_list = Column(JSON)
    parsed_data = Column(JSON, nullable=True)
    is_active = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)
    invoice_name = Column(String)
    font_map = Column(JSON, nullable=True)
    user = relationship("User", back_populates="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
