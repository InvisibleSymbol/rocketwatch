import i18n

i18n.load_path.append('./strings/')
i18n.set('skip_locale_root_data', True)
i18n.set('error_on_missing_translation', True)
i18n.set('file_format', 'json')
_ = i18n.t
