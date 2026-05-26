/* ═══════════════════════════════════════════════════════════════════
   REYDM – Main JavaScript
   ═══════════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
    // ─── Flash message auto-dismiss ──────────────────────────────
    document.querySelectorAll('.flash-msg').forEach(msg => {
        msg.addEventListener('click', () => msg.remove());
        setTimeout(() => {
            msg.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            msg.style.opacity = '0';
            msg.style.transform = 'translateX(100%)';
            setTimeout(() => msg.remove(), 300);
        }, 5000);
    });

    // ─── Mobile sidebar toggle ───────────────────────────────────
    const toggleBtn = document.querySelector('.mobile-toggle');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');

    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener('click', () => {
            sidebar.classList.toggle('open');
            overlay?.classList.toggle('show');
        });
        overlay?.addEventListener('click', () => {
            sidebar.classList.remove('open');
            overlay.classList.remove('show');
        });
    }

    // ─── OTP input handler ───────────────────────────────────────
    const otpInputs = document.querySelectorAll('.otp-input');
    const hiddenOtp = document.getElementById('otp-hidden');

    if (otpInputs.length > 0) {
        otpInputs.forEach((input, idx) => {
            input.addEventListener('input', (e) => {
                const val = e.target.value;
                if (val.length === 1 && idx < otpInputs.length - 1) {
                    otpInputs[idx + 1].focus();
                }
                updateHiddenOtp();
            });

            input.addEventListener('keydown', (e) => {
                if (e.key === 'Backspace' && !e.target.value && idx > 0) {
                    otpInputs[idx - 1].focus();
                }
            });

            input.addEventListener('paste', (e) => {
                e.preventDefault();
                const pasted = e.clipboardData.getData('text').trim();
                if (/^\d{6}$/.test(pasted)) {
                    otpInputs.forEach((inp, i) => { inp.value = pasted[i] || ''; });
                    otpInputs[5].focus();
                    updateHiddenOtp();
                }
            });
        });
    }

    function updateHiddenOtp() {
        if (hiddenOtp && otpInputs.length > 0) {
            hiddenOtp.value = Array.from(otpInputs).map(i => i.value).join('');
        }
    }

    // ─── Resend OTP ──────────────────────────────────────────────
    const resendBtn = document.getElementById('resend-otp-btn');
    if (resendBtn) {
        let cooldown = 0;
        resendBtn.addEventListener('click', async () => {
            if (cooldown > 0) return;
            try {
                const res = await fetch('/resend-otp', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    cooldown = 60;
                    resendBtn.disabled = true;
                    const interval = setInterval(() => {
                        cooldown--;
                        resendBtn.textContent = `Resend OTP (${cooldown}s)`;
                        if (cooldown <= 0) {
                            clearInterval(interval);
                            resendBtn.textContent = 'Resend OTP';
                            resendBtn.disabled = false;
                        }
                    }, 1000);
                }
            } catch (err) { console.error('Resend OTP error:', err); }
        });
    }

    // ─── Countdown timers for reminders ──────────────────────────
    const triggeredReminders = new Set();
    document.querySelectorAll('[data-countdown]').forEach(el => {
        const target = new Date(el.dataset.countdown).getTime();
        const reminderId = el.dataset.reminderId;

        function update() {
            const now = Date.now();
            const diff = target - now;

            if (diff <= 0) {
                el.textContent = 'Due now';
                el.style.color = 'var(--danger)';
                if (reminderId && !triggeredReminders.has(reminderId)) {
                    triggeredReminders.add(reminderId);
                    triggerReminderEmail(reminderId, el);
                }
                return;
            }

            const days = Math.floor(diff / 86400000);
            const hours = Math.floor((diff % 86400000) / 3600000);
            const mins = Math.floor((diff % 3600000) / 60000);
            const secs = Math.floor((diff % 60000) / 1000);

            let parts = [];
            if (days > 0) parts.push(`${days}d`);
            if (hours > 0) parts.push(`${hours}h`);
            parts.push(`${mins}m`);
            parts.push(`${secs}s`);
            el.textContent = parts.join(' ');
        }
        update();
        setInterval(update, 1000);
    });

    async function triggerReminderEmail(reminderId, el) {
        try {
            const res = await fetch(`/reminders/trigger/${reminderId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await res.json();
            if (data.success) {
                el.innerHTML = '<span class="badge badge-success" style="font-size:11px;">✓ Emails Sent</span>';
                const row = el.closest('tr');
                if (row) {
                    const statusCell = row.querySelector('td:nth-child(6)');
                    if (statusCell) statusCell.innerHTML = '<span class="badge badge-success">Sent</span>';
                }
            }
        } catch (err) { console.error('Trigger error:', err); }
    }

    // ─── Confirm delete ──────────────────────────────────────────
    document.querySelectorAll('[data-confirm]').forEach(el => {
        el.addEventListener('click', (e) => {
            if (!confirm(el.dataset.confirm)) e.preventDefault();
        });
    });

    // ─── Modals ──────────────────────────────────────────────────
    document.querySelectorAll('[data-modal]').forEach(trigger => {
        trigger.addEventListener('click', () => {
            const modal = document.getElementById(trigger.dataset.modal);
            if (modal) modal.classList.add('show');
        });
    });

    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.closest('.modal-backdrop').classList.remove('show');
        });
    });

    document.querySelectorAll('.modal-backdrop').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.remove('show');
        });
    });
});
