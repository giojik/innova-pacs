/** @type {AppTypes.Config} */
// PacsFlow custom OHIF — Innova, portal cookie (doctor_token / patient_auth). Keycloak ვარიანტი: app-config.js
const AE = 'RISINNOVA';
const dicomweb = `${window.location.origin}/dcm4chee-arc/aets/${AE}/rs`;

window.config = {
  name: 'Innova',
  routerBasename: '/',
  extensions: [],
  modes: [],
  customizationService: {
    global: ['@pacsflow/extension-pacsflow.customizationModule.pacsflow'],
  },
  pacsflow: {
    reportPanel: false,
    brand: 'Innova',
    portalUrl: '/login',
    grantHelp: false,
  },
  showStudyList: false,
  showPatientInfo: 'visible',
  investigationalUseDialog: { option: 'never' },
  maxNumberOfWebWorkers: 4,
  showWarningMessageForCrossOrigin: false,
  showCPUFallbackMessage: true,
  showLoadingIndicator: true,
  strictZSpacingForVolumeViewport: false,
  preferredStackViewport: true,
  useNorm16Texture: false,
  allowMultiSelectExport: false,
  maxNumRequests: { interaction: 100, thumbnail: 75, prefetch: 25 },
  showErrorDetails: 'never',
  defaultDataSourceName: 'dicomweb',
  whiteLabeling: {
    createLogoComponentFn: function (React) {
      return React.createElement(
        'span',
        { className: 'text-white', style: { cursor: 'default', fontSize: '18px', fontWeight: 700 } },
        'INNOVA ',
        React.createElement('span', { style: { color: '#4a7dff' } }, 'PACS')
      );
    },
  },
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        friendlyName: 'Innova PACS',
        name: 'dicomweb',
        wadoUriRoot: `${window.location.origin}/dcm4chee-arc/aets/${AE}/wado`,
        qidoRoot: dicomweb,
        wadoRoot: dicomweb,
        qidoSupportsIncludeField: true,
        supportsReject: false,
        dicomUploadEnabled: false,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
        supportsFuzzyMatching: false,
        supportsWildcard: true,
        singlepart: 'video,bulkdata',
        omitQuotationForMultipartRequest: true,
        bulkDataURI: { enabled: true },
      },
    },
  ],
  httpErrorHandler: error => {
    if (window.__pacsflow && window.__pacsflow.handleHttpError) {
      window.__pacsflow.handleHttpError(error);
    }
  },
};
