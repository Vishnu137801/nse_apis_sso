from sqlalchemy import Column, String, Integer, Text, DateTime
from sqlalchemy.sql import func
from database import Base


class AOFImageUpload(Base):
    __tablename__ = "aof_image_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_code = Column(String(10), index=True)
    file_name = Column(String(100))
    document_type = Column(String(50))
    status = Column(String(50))
    message = Column(Text)
    lendrix_uuid = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
