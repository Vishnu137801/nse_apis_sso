from sqlalchemy import Column, String, DateTime
from sqlalchemy.sql import func
from database import Base


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"

    lendrix_uuid = Column(String(36), primary_key=True)
    email = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    phone = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
