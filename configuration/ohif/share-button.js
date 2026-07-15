(function() {
    // 1. ფაილის სახელის ფორმატირება (თარიღი_ინიციალი_გვარი_მეთოდი_პირადინომერი)
    function formatFileName(data) {
        const dateClean = (data.date || "").replace(/[^0-9]/g, '').substring(0, 8);
        const modalityClean = (data.modality || "STUDY").replace(/[\\\/]/g, '-');
        let namePart = "Patient";
        if (data.name && data.name.includes('^')) {
            const parts = data.name.split('^');
            namePart = (parts[1] ? parts[1].charAt(0) : "P") + "_" + parts[0];
        } else if (data.name && data.name.includes(',')) {
            const parts = data.name.split(',');
            namePart = parts[1].trim().charAt(0) + "_" + parts[0].trim();
        }
        return `${dateClean}_${namePart}_${modalityClean}_${data.pid}.zip`;
    }

    // 2. ბეჭდვის ფუნქცია (QR კოდი)
    function printQRCode(data) {
        const printWindow = window.open('', '_blank');
        const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(data.url)}`;
        printWindow.document.write(`<html><head><title>Innova Medical</title><style>body { font-family: sans-serif; padding: 50px; line-height: 1.8; } .info-row b { width: 200px; display: inline-block; }</style></head><body onload="setTimeout(() => { window.print(); window.close(); }, 500);"><h1>Innova Medical Center</h1><p><b>პირადი ნომერი:</b> ${data.pid}</p><p><b>პაციენტი:</b> ${data.name}</p><p><b>კვლევა:</b> ${data.study}</p><div style="text-align:center; margin:40px;"><img src="${qrUrl}" width="250"></div><p>შეიყვანეთ მონაცემები პორტალზე და ნახეთ კვლევა.</p></body></html>`);
        printWindow.document.close();
    }

    // 3. გაზიარების მოდალური ფანჯარა
    function showShareModal(patientData) {
        const existing = document.getElementById('share-modal-overlay');
        if (existing) existing.remove();
        const overlay = document.createElement('div');
        overlay.id = 'share-modal-overlay';
        overlay.style = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index:10000; display:flex; align-items:center; justify-content:center; font-family:sans-serif;';
        const modal = document.createElement('div');
        modal.style = 'background:white; padding:30px; border-radius:20px; width:400px; text-align:center; position:relative;';
        modal.innerHTML = `<span id="close-modal" style="position:absolute; top:10px; right:20px; cursor:pointer; font-size:24px; color:#aaa;">&times;</span><h2 style="color:#004a99;">გაზიარება</h2><div style="text-align:left; background:#f8f9fa; padding:15px; border-radius:10px; margin-bottom:20px;"><p><b>პაციენტი:</b> ${patientData.name}</p><p><b>პირადი ნომერი:</b> ${patientData.pid}</p></div><input type="email" id="target-email" placeholder="იმეილი" style="width:100%; padding:12px; border:1px solid #ddd; border-radius:10px; margin-bottom:20px;"><div style="display:flex; gap:10px;"><button id="btn-email" style="flex:1; padding:12px; background:#004a99; color:white; border:none; border-radius:10px; cursor:pointer;">📧 გაგზავნა</button><button id="btn-print" style="flex:1; padding:12px; background:#28a745; color:white; border:none; border-radius:10px; cursor:pointer;">🖨️ ბეჭდვა</button></div>`;
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        document.getElementById('close-modal').onclick = () => overlay.remove();
        document.getElementById('btn-print').onclick = () => printQRCode(patientData);
        document.getElementById('btn-email').onclick = async () => {
            const email = document.getElementById('target-email').value;
            if(!email) return alert('შეიყვანეთ იმეილი');
            
            // 1. ვპოულობთ დალოგინებულ ექიმს (OHIF-ის ინტერფეისიდან)
            let senderName = "უცნობი ექიმი";
            try {
                // Keycloak ინახავს მომხმარებლის ინფოს sessionStorage-ში
                // ვეძებთ გასაღებს, რომელიც იწყება "oidc.user"
                const oidcKey = Object.keys(sessionStorage).find(key => key.startsWith('oidc.user'));
                if (oidcKey) {
                    const userData = JSON.parse(sessionStorage.getItem(oidcKey));
                    // ვიღებთ preferred_username-ს (ეს არის ლოგინი, მაგ: gchutkerashvili)
                    // ან 'name'-ს (თუ AD-დან სრული სახელი მოდის)
                    senderName = userData.profile.preferred_username || userData.profile.name || "უცნობი ექიმი";
                }
            } catch (e) {
                console.error("Error reading session:", e);
            }

            // ვამოწმებთ, რომ ლინკი ნამდვილად არსებობს
            const finalPortalUrl = patientData.url; 
            console.log("Sending URL to server:", finalPortalUrl); // დიაგნოსტიკისთვის
            
            const btn = document.getElementById('btn-email');
            btn.innerText = 'გაიგზავნება...';
            btn.disabled = true;
            
            const formData = new FormData();
            formData.append('email', email);
            formData.append('subject', "თქვენი კვლევის შედეგი - Innova Medical Centre");
            formData.append('body', finalPortalUrl); // აქ ვაგზავნით სრულ HTTPS ლინკს
            formData.append('p_name', patientData.name); // <--- დაამატეთ ეს
            formData.append('p_id', patientData.pid);     // <--- დაამატეთ ეს
            formData.append('sender_name', senderName);

            try {
                const response = await fetch('/p/send-email', { method: 'POST', body: formData });
                if (response.ok) { 
                    alert('იმეილი გაიგზავნა!'); 
                    overlay.remove(); 
                } else {
                    alert('შეცდომა გაგზავნისას.');
                }
            } catch (err) {
                alert('კავშირის შეცდომა.');
            } finally {
                btn.innerText = '📧 გაგზავნა';
                btn.disabled = false;
            }

        };
        const expandedRow = btn.closest('tr');
        const mainRow = expandedRow.previousElementSibling;
        const cells = mainRow.querySelectorAll('td');

        const pData = {
            name: cells[0]?.innerText.trim() || "Unknown",
            pid: cells[1]?.innerText.trim() || "---",
        }
    }   

    // 4. პაციენტის სახელის "ძალით" დაწერა სურათზე (Overlay)
    function injectPatientOverlay() {
        if (!window.location.pathname.includes('/viewer')) return;
        const sidebarName = document.querySelector('.text-base.text-white.truncate')?.innerText;
        const studyUid = new URLSearchParams(window.location.search).get('StudyInstanceUIDs');
        if (!sidebarName) return;

        // ვეძებთ OHIF-ის ოვერლეის კონტეინერებს
        const overlays = document.querySelectorAll('.viewport-overlay');
        overlays.forEach(overlay => {
            if (overlay.classList.contains('top-left') || (overlay.style.top === '0px' && overlay.style.left === '0px')) {
                let customBlock = overlay.querySelector('#innova-overlay');
                if (!customBlock) {
                    customBlock = document.createElement('div');
                    customBlock.id = 'innova-overlay';
                    customBlock.style = 'color:#00ff00; font-weight:bold; font-size:16px; margin-bottom:5px; text-shadow:1px 1px 2px black;';
                    overlay.prepend(customBlock);
                }
                const cleanName = sidebarName.replace(/\^/g, ' ');
                const shortId = studyUid ? studyUid.split('.').pop().substring(0, 10) : '---';
                customBlock.innerText = `${cleanName} (ID: ${shortId}...)`;
            }
        });
    }

    // 5. ღილაკები Study List-ში (ექიმებისთვის)
    function addButtonsToList() {
        const buttons = document.querySelectorAll('button');
        buttons.forEach(btn => {
            if (btn.innerText.includes('Basic Viewer') && !btn.parentElement.querySelector('.custom-share-btn')) {
                const urlStr = (btn.closest('a') || btn.querySelector('a') || {href: window.location.href}).href;
                const studyUid = new URL(urlStr).searchParams.get('StudyInstanceUIDs');
                if (studyUid) {
                    const expandedRow = btn.closest('tr');
                    const mainRow = expandedRow.previousElementSibling;
                    const cells = mainRow.querySelectorAll('td');
                    const pData = {
                        name: cells[0]?.innerText || "Unknown",
                        pid: cells[1]?.innerText || "000",
                        date: cells[2]?.innerText || "000",
                        modality: cells[4]?.innerText || "STUDY",
                        study: cells[4]?.innerText || "კვლევა"
                    };

                    const shareBtn = document.createElement('button');
                    shareBtn.innerHTML = '🔗 გაზიარება'; shareBtn.className = 'custom-share-btn';
                    shareBtn.style = 'margin-left:10px; background:#28a745; color:white; padding:5px 12px; border-radius:5px; border:none; cursor:pointer; font-weight:bold; font-size:13px;';
                    shareBtn.onclick = (event) => {
                        event.stopPropagation(); event.preventDefault();
                        showShareModal({ ...pData, url: window.location.origin + '/p/' + studyUid });
                    };
                    btn.parentElement.appendChild(shareBtn);

                    const downBtn = document.createElement('button');
                    downBtn.innerHTML = '📥 ჩამოტვირთვა'; downBtn.className = 'custom-download-btn';
                    downBtn.style = 'margin-left:10px; background:#007bff; color:white; padding:5px 12px; border-radius:5px; border:none; cursor:pointer; font-weight:bold; font-size:13px;';
                    downBtn.onclick = (event) => {
                        
                        event.stopPropagation();
                        event.preventDefault();
                        const downloadUrl = `/p/download-zip/${studyUid}/auto`;
                        window.open(downloadUrl, '_blank');
                    };
                    btn.parentElement.appendChild(downBtn);
                }
            }
        });
    }

    // 6. ჩამოტვირთვის ღილაკი Viewer-ში (პაციენტისთვის)
    function addDownloadToViewer() {
            if (window.location.pathname.includes('/viewer')) {
            const headerLeft = document.querySelector('.flex.flex-row.items-center');
            if (headerLeft && !document.querySelector('.viewer-download-btn')) {
                const studyUid = new URLSearchParams(window.location.search).get('StudyInstanceUIDs');
                if (studyUid) {
                    const btn = document.createElement('button');
                    btn.innerHTML = '📥 კვლევის გადმოწერა (ZIP)';
                    btn.className = 'viewer-download-btn';
                    btn.style = 'margin-left:30px; background:#28a745; color:white; padding:6px 12px; border-radius:5px; cursor:pointer; border:none; font-weight:bold; font-size:13px; z-index:9999;';
                    btn.onclick = (event) => {
                        event.stopPropagation();
                        event.preventDefault();
                        const downloadUrl = `/p/download-zip/${studyUid}/auto`;
                        window.open(downloadUrl, '_blank');
                    };
                    headerLeft.appendChild(btn);
                }
            }
        }
    }

    // პერიოდული განახლება
    setInterval(() => {
        if (window.location.pathname.includes('/viewer')) {
            injectPatientOverlay();
            addDownloadToViewer();
        } else {
            addButtonsToList();
        }
    }, 1500);
})();