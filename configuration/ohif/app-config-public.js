window.config = {
  routerBasename: '/',
  showStudyList: false, // პაციენტმა სხვა პაციენტების სია არ უნდა ნახოს
  
  // აი ეს ორი ხაზი აკლდა და იწვევდა ერორს:
  extensions: [],
  modes: [],

  showLoadingIndicator: true,
  
  // DICOMweb კავშირი (ავტორიზაციის გარეშე პაციენტისთვის)
  dataSources: [
    {
      friendlyName: 'DCM4CHEE Server',
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        name: 'DCM4CHEE',
        wadoUriRoot: 'https://ris.innovamedical.ge/dcm4chee-arc/aets/RISINNOVA/wado',
        qidoRoot: 'https://ris.innovamedical.ge/dcm4chee-arc/aets/RISINNOVA/rs',
        wadoRoot: 'https://ris.innovamedical.ge/dcm4chee-arc/aets/RISINNOVA/rs',
        qidoSupportsIncludeField: true,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
        supportsFuzzyMatching: true,
        supportsWildcard: true,
        singlepart: 'video,bulkdata',
        omitQuotationForMultipartRequest: true,
      },
    },
  ],
  customizationService: [
    {
      id: 'ohif.overlayItem',
      content: ({ instance }) => {
        if (!instance) return '';
        const name = instance.PatientName?.Alphabetic || instance.PatientName || '';
        const cleanName = name.replace(/\^/g, ' '); // ასუფთავებს DICOM-ის ^ ნიშნებს
        const patientID = instance.PatientID || '';
        return `${cleanName} (${patientID})`;
      },
      label: '',
      location: 'topLeft', // ათავსებს მარცხენა ზედა კუთხეში
      priority: 100, // პრიორიტეტი, რომ ყველაზე მაღლა იყოს
    },
  ],
  defaultDataSourceName: 'dicomweb',
};