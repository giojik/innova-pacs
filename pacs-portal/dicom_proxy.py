import os
from pynetdicom import AE, evt, debug_logger
from pynetdicom.sop_class import (
    Verification,
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    DigitalMammographyXRayImageStorageForPresentation,
    CTImageStorage,
    MRImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    SecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
    XRayRadiofluoroscopicImageStorage
)

DEST_PACS_HOST = "arc"
DEST_PACS_PORT = 11112
DEST_AE_TITLE  = "RISINNOVA"

SECONDARY_CAPTURE_UID = '1.2.840.10008.5.1.4.1.1.7'

def handle_echo(event):
    return 0x0000

def handle_store(event):
    ds = event.dataset
    ds.file_meta = event.file_meta

    # ⚡ 1. სახელის გასწორება: = -> ^
    if 'PatientName' in ds:
        original_name = str(ds.PatientName)
        if "=" in original_name:
            ds.PatientName = original_name.replace('=', '^')
            print(f" სახელი გასწორდა: {original_name} -> {ds.PatientName}")
        else:
            print(f" მიღებულია: {ds.PatientName}")

    # ⚡ 2. ჰოსპიტალის სახელის გასწორება
    ds.InstitutionName = "Innova Medical Center"
    ds.StationName = "SHIMADZU_FIXED"

    # ⚡ 3. BMD Modality fix — Primus OSTEOSYS დენსიტომეტრი
    current_modality = str(getattr(ds, 'Modality', '') or '')
    sop_uid = ''
    try:
        sop_uid = str(ds.file_meta.MediaStorageSOPClassUID)
    except:
        sop_uid = str(getattr(ds, 'SOPClassUID', ''))

    manufacturer = str(getattr(ds, 'Manufacturer', '')).upper()
    model        = str(getattr(ds, 'ManufacturerModelName', '')).upper()
    is_osteosys  = any(k in manufacturer or k in model
                       for k in ('OSTEOSYS', 'PRIMUS', 'BMD', 'OSTEO'))

    if (not current_modality or current_modality in ('OT', 'SC', '')) and \
       (sop_uid == SECONDARY_CAPTURE_UID or is_osteosys):
        ds.Modality = 'BMD'
        print(f"⚡ Modality BMD დაყენდა (იყო: '{current_modality}', manufacturer: '{manufacturer}', model: '{model}')")

    # ⚡ 4. გაგზავნა მთავარ PACS-ზე
    ae = AE(ae_title="SHIMADZU_FIXER")
    ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)

    assoc = ae.associate(DEST_PACS_HOST, DEST_PACS_PORT, ae_title=DEST_AE_TITLE)
    if assoc.is_established:
        status = assoc.send_c_store(ds)
        assoc.release()
        print(f"✅ ფაილი გასწორდა და გადაეგზავნა RISINNOVA-ს")
        return 0x0000
    else:
        print(f"❌ ვერ მოხერხდა კავშირი მთავარ PACS-თან (arc:11112)")
        return 0xC000

ae = AE(ae_title="SHIMADZU_PROXY")

all_transfer_syntaxes = [
    '1.2.840.10008.1.2',
    '1.2.840.10008.1.2.1',
    '1.2.840.10008.1.2.4.50',
    '1.2.840.10008.1.2.4.70',
    '1.2.840.10008.1.2.4.80',
    '1.2.840.10008.1.2.4.90',
    '1.2.840.10008.1.2.5',
]

supported_sops = [
    Verification,
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    DigitalMammographyXRayImageStorageForPresentation,
    CTImageStorage,
    MRImageStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
    SecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
    XRayRadiofluoroscopicImageStorage
]

for sop in supported_sops:
    ae.add_supported_context(sop, all_transfer_syntaxes)

handlers = [
    (evt.EVT_C_ECHO, handle_echo),
    (evt.EVT_C_STORE, handle_store)
]

print(" Universal DICOM Proxy (Fixer) ჩაირთო 0.0.0.0:11115-ზე...")
ae.start_server(('0.0.0.0', 11115), block=True, evt_handlers=handlers)
