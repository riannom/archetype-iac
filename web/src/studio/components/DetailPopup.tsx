import React from 'react';
import Modal from '../../components/ui/Modal';

interface DetailPopupProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: string;
}

const DetailPopup: React.FC<DetailPopupProps> = ({
  isOpen,
  onClose,
  title,
  children,
  width = "max-w-lg",
}) => (
  <Modal
    isOpen={isOpen}
    onClose={onClose}
    title={title}
    size="full"
    className={width}
  >
    {children}
  </Modal>
);

export default DetailPopup;
