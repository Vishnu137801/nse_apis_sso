from sqlalchemy import Column, String, Integer, Text, DateTime
from sqlalchemy.sql import func
from database import Base


class ELOGUpload(Base):
    __tablename__ = "elog_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_code = Column(String(10), index=True)
    primary_holder_pan = Column(String(10))
    primary_holder_ip_address = Column(String(45))
    primary_holder_log_date = Column(String(20))
    second_holder_pan = Column(String(10), nullable=True)
    second_holder_ip_address = Column(String(45), nullable=True)
    second_holder_log_date = Column(String(20), nullable=True)
    third_holder_pan = Column(String(10), nullable=True)
    third_holder_ip_address = Column(String(45), nullable=True)
    third_holder_log_date = Column(String(20), nullable=True)
    elog_status = Column(String(50), nullable=True)
    elog_status_message = Column(Text, nullable=True)
    lendrix_uuid = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
