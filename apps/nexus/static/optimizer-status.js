const badge = document.getElementById("status-badge")
const id = badge.dataset.id
const checkStatus = async () => {
    const status = await fetch(`/optimize/${id}/status`).then(r => r.text())

    if (status == 'running'){
        badge.textContent = '🟡 Running'
        badge.style.color = 'orange'
        setTimeout(checkStatus, 5000)
    }
    else {
        badge.textContent = '🟢 Idle'
        badge.style.color = 'green'
    }
}

checkStatus()