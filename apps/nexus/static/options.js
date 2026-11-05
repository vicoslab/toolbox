const setOptions = document.getElementById("set-options");
const listOptions = document.getElementById("list-options");
const addOption = document.querySelector(".edit-options > button");
let values, values_display, options, completions;

function checkRequiredOptions(stage) {
    for (const [k, v] of Object.entries(options)) {
        if (v.required?.includes(stage) && !(k in values)) {
            showToast("error", `Missing required option '${v.title}'`);
            return false;
        }
    }
    return true;
}

function display(k, v) {
    const prop = options[k];
    if (prop === undefined) return;

    const wrap = document.createElement("button");
    wrap.addEventListener("click", () => editOption(k, prop));
    wrap.className = "shadow";
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.padding = "0.25rem 0.5rem 0.25rem 1rem"
    wrap.title = prop.description || "";

    const name = document.createElement("span");
    name.className = "tag-name";
    name.innerText = prop.title;

    const value = document.createElement("span");
    value.className = "tag-value";
    value.innerText = v;

    const remove = document.createElement("button");
    remove.title = "Remove option";
    remove.innerHTML = `<img src="/static/icons/remove.svg" class="shadow-sm">`;
    remove.className = "transparent";
    remove.style.width = "1rem";
    remove.style.height = "1rem";
    remove.style.marginLeft = "0.5rem";
    remove.style.padding = 0;
    remove.addEventListener("click", e => {
        if (k in values) {
            delete values[k];
            delete values_display[k];
            wrap.remove();
        }
        e.stopPropagation();
    });
    wrap.append(name, value, remove);
    return wrap;
}

function setValue(k, v) {
    console.log("setValue", k, v);
    values[k] = v;
    let el = values_display[k];
    if (el === undefined) {
        el = display(k, v);
        values_display[k] = el;
        setOptions.append(el);
    } else {
        values_display[k].querySelector(".tag-value").innerText = v;
    }
}

function editOption(k, prop) {
    const title = document.createElement("div");
    title.className = "option-title";
    title.innerText = prop.title;

    const description = document.createElement("p");
    description.className = "option-description";
    description.innerText = prop.description || "";

    if (prop.enum) {
        const opts = document.createElement("div");
        for (const opt of prop.enum) {
            const radio = document.createElement("input");
            radio.name = k;
            radio.type = "radio";
            radio.id = opt;
            radio.value = opt;
            radio.checked = (values[k] ?? prop.default) === opt;

            const radio_label = document.createElement("label");
            radio_label.htmlFor = opt;
            radio_label.innerText = opt;
            opts.append(radio, radio_label);
        }
        showModal(title, description, opts).then(() => setValue(k, opts.querySelector('input:checked').value)).catch(() => { });
        return;
    }
    switch (prop.type) {
        case "string": {
            const input = dropdown({ name: k, completions: completions[k], placeholder: prop.default, value: values[k] });
            showModal(title, description, input).then(() => setValue(k, input.value)).catch(() => { });
            break;
        }
        case "boolean": {
            const radio_yes = document.createElement("input");
            radio_yes.name = k;
            radio_yes.type = "radio";
            radio_yes.checked = !!(values[k] ?? prop.default ?? false);
            const radio_yes_label = document.createElement("label");
            radio_yes_label.append(radio_yes, "true");

            const radio_no = document.createElement("input");
            radio_no.name = k;
            radio_no.type = "radio";
            radio_no.checked = !(values[k] ?? prop.default ?? false);
            const radio_no_label = document.createElement("label");
            radio_no_label.append(radio_no, "false");

            showModal(title, description, radio_yes_label, radio_no_label).then(() => setValue(k, radio_yes.checked === true)).catch(() => { });
            break;
        }
        case "number":
        case "integer": {
            const input = document.createElement("input");
            input.name = k;
            input.inputMode = prop.type === "integer" ? "numeric" : "decimal";
            input.pattern = prop.type === "integer" ? "\d*" : "\d+(.\d+)?";
            input.placeholder = prop.default ?? "";
            input.value = values[k] ?? null;
            showModal(title, description, input).then(() => setValue(k, input.value)).catch(() => { });
            break;
        }
        case "array": {
            if (prop.items?.enum) {
                const opts = document.createElement("div");
                for (const opt of prop.items?.enum) {
                    const box = document.createElement("input");
                    box.name = k;
                    box.type = "checkbox";
                    box.value = opt;
                    box.checked = (values[k] ?? prop.default ?? []).includes(opt);

                    const box_label = document.createElement("label");
                    box_label.append(box, opt);
                    opts.append(box_label);
                }
                showModal(title, description, opts).then(() => setValue(k, [...opts.querySelectorAll('input:checked')].map(x => x.value))).catch(() => { });
                return;
            }
            showToast("error", `model-options: Invalid item in array type: '${prop.items}'`)
            break;
        }
        default: showToast("error", `model-options: Invalid type '${prop.type}'`);
    }
}

async function showOptions(stage, url, key) {
    ({ options, completions } = await fetch(url).then(r => r.json()));
    document.querySelector(".edit-options").style.display = Object.keys(options).length > 0 ? "" : "none";

    values = JSON.parse(localStorage.getItem(key) || "{}");
    values_display = {};
    setOptions.replaceChildren();

    if (stage !== undefined) {
        for (const [k, v] of Object.entries(options)) {
            if ("stage" in v && v.stage !== stage) {
                delete options[k];
            }
        }
        for (const [k, v] of Object.entries(completions)) {
            if ("stage" in v && v.stage !== stage) {
                delete completions[k];
            }
        }
    }

    for (const [k, [autofill, compls]] of Object.entries(completions)) {
        if (autofill !== null) {
            values[k] = autofill;
        }
    }
    for (const [k, v] of Object.entries(values)) {
        // k may have been removed due to train/infer only options
        if (k in options) {
            const el = display(k, v);
            values_display[k] = el;
            setOptions.append(el);
        }
    }
}

(() => {
    addOption.addEventListener("click", () => {
        listOptions.replaceChildren(...Object.keys(options).filter(k => !(k in values)).sort((a, b) => options[a].title.localeCompare(options[b].title, undefined, { numeric: true, sensitivity: "base" })).map(k => {
            const prop = options[k];
            const el = document.createElement("div");
            el.innerText = prop.title;
            el.title = prop.description || "";
            el.addEventListener("click", e => {
                e.stopPropagation();
                listOptions.hidePopover();
                editOption(k, prop);
            });
            return el;
        }));
        listOptions.showPopover();
        const { left, right, top, bottom } = addOption.getBoundingClientRect();
        if (top + addOption.offsetHeight + listOptions.offsetHeight < addOption.offsetParent.offsetHeight) {
            listOptions.style.top = top + addOption.offsetHeight + "px";
            listOptions.style.marginTop = "0.5rem";
        } else {
            listOptions.style.top = top - listOptions.offsetHeight + "px";
            listOptions.style.marginTop = "-0.5rem";
        }
        if (left + listOptions.offsetWidth < addOption.offsetParent.offsetWidth) {
            listOptions.style.left = left + "px";
        } else {
            listOptions.style.left = left + addOption.offsetWidth - listOptions.offsetWidth + "px";
        }
    });
})();
