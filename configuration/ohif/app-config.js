window.config = {
  routerBasename: '/',
  showStudyList: true,
  
  defaultLanguage: 'ka',
  supportedLanguages: ['en', 'ka'],
  
  // 1. Keycloak ავტორიზაციის კონფიგურაცია
  oidc: [
    {
      customAuthorityConfig: {
        revokeEndpoint: 'https://ris.innovamedical.ge/realms/dcm4che/protocol/openid-connect/revoke',
      },
      authority: 'https://ris.innovamedical.ge/realms/dcm4che',
      client_id: 'ohif-viewer', // <-- ზუსტად ის სახელი, რაც Keycloak-ში შევქმენით
      redirect_uri: 'https://ris.innovamedical.ge/callback',
      response_type: 'code',
      scope: 'openid profile email',
      post_logout_redirect_uri: 'https://ris.innovamedical.ge/',
      revokeUri: 'https://ris.innovamedical.ge/realms/dcm4che/protocol/openid-connect/revoke',
      automaticSilentRenew: true,
      revokeAccessTokenOnSignout: true,
    },
  ],

  extensions: [],
  modes: [],
  
  // 2. კავშირი DCM4CHEE (PACS) სერვერთან სურათების წამოსაღებად
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
        maxNumberOfWebWorkers: 4, // ბრაუზერის ბირთვების რაოდენობა სურათების დეკოდირებისთვის
        decodeConfig: {
        usePDFJS: false,
        },
        singlepart: 'video,bulkdata',
        omitQuotationForMultipartRequest: true,
        acceptHeader: ['video/mp4', 'application/dicom+json', 'multipart/related; type="application/octet-stream"'],
        bulkDataURI: {
        enabled: true,
        },
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

    // 2. აქ ვეუბნებით, რომ ზედა მარცხენა კუთხეში (topLeft) გამოჩნდეს ეს ინფორმაცია
    'viewportOverlay.topLeft': {
      items: [
        {
          id: 'patientInfo', // იძახებს ზემოთ განსაზღვრულ 'patientInfo'-ს
        },
        {
          id: 'StudyDate', // ტოვებს ორიგინალ თარიღსაც
        },
      ],
    },
  }, 
  defaultDataSourceName: 'dicomweb',
};
