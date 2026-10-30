import {getDocument,GlobalWorkerOptions} from 'pdfjs-dist';
GlobalWorkerOptions.workerSrc='/vendor/pdf.worker.min.mjs';
export const load=url=>getDocument({url,standardFontDataUrl:'/vendor/standard_fonts/'});
